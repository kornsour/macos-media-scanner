"""Multi-stage duplicate detection pipeline."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from media_scanner.core.hasher import (
    dhash_image,
    dhash_image_small,
    hamming_distance,
    hamming_distance_int,
    hash_hex_to_int,
    noise_level,
    phash_image,
    phash_image_small,
    sha256_file,
)
from media_scanner.core.parallel import (
    compute_hashes_parallel,
    compute_video_hashes_parallel,
)
from media_scanner.core.video_hasher import dhash_video, sha256_video, video_frames_similar
from media_scanner.data.models import DuplicateGroup, MatchType, MediaItem, MediaType

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_scanner.config import Config
    from media_scanner.data.cache import CacheDB

logger = logging.getLogger(__name__)


def _fmt_size(n: int) -> str:
    """Format bytes as human-readable size for log messages."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _default_max_workers(config: Config | None) -> int:
    """Get max_workers from config, falling back to 4."""
    if config is not None and hasattr(config, "max_workers"):
        return config.max_workers
    return 4


def _adaptive_threshold(
    item_a: MediaItem, item_b: MediaItem,
    base_threshold: int, config: Config,
) -> int:
    """Widen threshold when resolution ratio exceeds configured limit."""
    pixels_a = item_a.width * item_a.height
    pixels_b = item_b.width * item_b.height
    if pixels_a == 0 or pixels_b == 0:
        return base_threshold
    ratio = max(pixels_a, pixels_b) / min(pixels_a, pixels_b)
    if ratio > config.resolution_ratio_threshold:
        return int(base_threshold * config.resolution_adaptive_factor)
    return base_threshold


def find_exact_duplicates(
    cache: CacheDB,
    config: Config | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """Stage 1+2: Group by file size, then SHA-256 within groups.

    Only computes hashes for files that share a size with another file.
    """
    groups: list[DuplicateGroup] = []
    size_groups = cache.get_size_groups(min_group_size=2)
    max_workers = _default_max_workers(config)

    # Phase 1: Collect all items and identify those needing hashing
    needs_hash: list[tuple[str, str]] = []
    all_size_items: dict[int, list[MediaItem]] = {}

    for file_size, uuids in size_groups.items():
        items_in_group = []
        for uuid in uuids:
            item = cache.get_item(uuid)
            if not item or not item.path or not item.path.exists():
                continue
            items_in_group.append(item)
            if not item.sha256:
                needs_hash.append((uuid, str(item.path)))
        if items_in_group:
            all_size_items[file_size] = items_in_group

    total_items = sum(len(items) for items in all_size_items.values())

    # Phase 2: Compute missing hashes in parallel
    new_hashes = compute_hashes_parallel(
        work_items=needs_hash,
        hash_fn=sha256_file,
        hash_type="sha256",
        cache=cache,
        max_workers=max_workers,
        progress_callback=progress_callback if needs_hash else None,
    )

    # Phase 3: Group by SHA-256
    processed = len(needs_hash)
    for file_size, items in all_size_items.items():
        sha_map: dict[str, list[MediaItem]] = defaultdict(list)
        for item in items:
            sha = item.sha256 or new_hashes.get(item.uuid)
            if sha:
                sha_map[sha].append(item)
            if item.sha256:
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_items)

        for sha, matched in sha_map.items():
            if len(matched) >= 2:
                groups.append(DuplicateGroup(
                    group_id=0, match_type=MatchType.EXACT, items=matched,
                ))

    return groups


def find_near_duplicates(
    cache: CacheDB,
    config: Config,
    progress_callback: Callable[[int, int], None] | None = None,
    compare_progress_callback: Callable[[int, int], None] | None = None,
    phash_progress_callback: Callable[[int, int], None] | None = None,
    exclude_uuids: set[str] | None = None,
) -> list[DuplicateGroup]:
    """Stage 3+4: dHash grouping with pHash confirmation for photos.

    Operates on photos that weren't caught as exact duplicates.
    Pass *exclude_uuids* (e.g. from exact-duplicate groups) to skip items
    that are already grouped, avoiding redundant comparisons and overlapping groups.
    """
    _excluded = exclude_uuids or set()

    # Get all photos that have a path, excluding already-grouped items
    all_items = cache.get_all_items()
    photos = [
        i for i in all_items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
        and i.uuid not in _excluded
    ]

    # Identify photos needing dHash computation
    needs_dhash: list[tuple[str, str]] = []
    for item in photos:
        if not item.dhash:
            needs_dhash.append((item.uuid, str(item.path)))

    # Compute dHash in parallel
    max_workers = _default_max_workers(config)
    new_dhashes = compute_hashes_parallel(
        work_items=needs_dhash,
        hash_fn=dhash_image,
        hash_type="dhash",
        cache=cache,
        max_workers=max_workers,
        progress_callback=progress_callback,
    )

    # Merge new hashes back into item objects
    for item in photos:
        if not item.dhash and item.uuid in new_dhashes:
            item.dhash = new_dhashes[item.uuid]

    # Dual-scale: compute small dHash (hash_size=8, 64-bit) for all photos
    needs_dhash_small: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in photos
        if not item.dhash_small and item.path
    ]
    if needs_dhash_small:
        new_small = compute_hashes_parallel(
            work_items=needs_dhash_small,
            hash_fn=dhash_image_small,
            hash_type="dhash_small",
            cache=cache,
            max_workers=max_workers,
        )
        for item in photos:
            if not item.dhash_small and item.uuid in new_small:
                item.dhash_small = new_small[item.uuid]

    # Group by similar dHash — pre-convert to ints for fast comparison
    hashed_photos = [p for p in photos if p.dhash]
    hash_ints = [hash_hex_to_int(p.dhash) for p in hashed_photos]
    # Dual-scale: small hash ints (may be None)
    small_hash_ints = [
        hash_hex_to_int(p.dhash_small) if p.dhash_small else None
        for p in hashed_photos
    ]
    n = len(hashed_photos)
    threshold = config.dhash_threshold
    small_threshold = config.dhash_small_threshold

    groups: list[DuplicateGroup] = []

    # Union-Find for proper transitive clustering — if A~B and B~C,
    # all three end up in the same group regardless of iteration order.
    parent: list[int] = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        if compare_progress_callback:
            compare_progress_callback(i + 1, n)
        ha = hash_ints[i]
        ha_small = small_hash_ints[i]
        for j in range(i + 1, n):
            # Resolution-adaptive threshold for 256-bit comparison
            adaptive_thresh = _adaptive_threshold(
                hashed_photos[i], hashed_photos[j], threshold, config,
            )
            matched = hamming_distance_int(ha, hash_ints[j]) <= adaptive_thresh
            # Dual-scale fallback: check 64-bit hashes
            if not matched and ha_small is not None and small_hash_ints[j] is not None:
                matched = hamming_distance_int(ha_small, small_hash_ints[j]) <= small_threshold
            if matched:
                _union(i, j)

    # Collect clusters from union-find
    cluster_map: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        cluster_map[_find(i)].append(i)

    multi_clusters = [
        indices for indices in cluster_map.values() if len(indices) >= 2
    ]

    # Batch-compute pHash in parallel for all items in clusters
    cluster_items_by_uuid: dict[str, MediaItem] = {}
    for indices in multi_clusters:
        for idx in indices:
            item = hashed_photos[idx]
            cluster_items_by_uuid[item.uuid] = item

    needs_phash: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in cluster_items_by_uuid.values()
        if not item.phash and item.path and item.path.exists()
    ]

    if needs_phash:
        new_phashes = compute_hashes_parallel(
            work_items=needs_phash,
            hash_fn=phash_image,
            hash_type="phash",
            cache=cache,
            max_workers=max_workers,
            progress_callback=phash_progress_callback,
        )
        for uuid, ph in new_phashes.items():
            if uuid in cluster_items_by_uuid:
                cluster_items_by_uuid[uuid].phash = ph

    # Dual-scale: compute small pHash for clustered items
    needs_phash_small: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in cluster_items_by_uuid.values()
        if not item.phash_small and item.path and item.path.exists()
    ]
    if needs_phash_small:
        new_phashes_small = compute_hashes_parallel(
            work_items=needs_phash_small,
            hash_fn=phash_image_small,
            hash_type="phash_small",
            cache=cache,
            max_workers=max_workers,
        )
        for uuid, ph in new_phashes_small.items():
            if uuid in cluster_items_by_uuid:
                cluster_items_by_uuid[uuid].phash_small = ph

    for indices in multi_clusters:
        cluster = [hashed_photos[i] for i in indices]
        # Stage 4: pHash confirmation (hashes already computed above)
        confirmed = _confirm_with_phash(cluster, cache, config)
        if len(confirmed) >= 2:
            groups.append(
                DuplicateGroup(
                    group_id=0,
                    match_type=MatchType.NEAR,
                    items=confirmed,
                )
            )

    return groups


def _confirm_with_phash(
    candidates: list[MediaItem],
    cache: CacheDB,
    config: Config,
) -> list[MediaItem]:
    """Confirm near-duplicates using pHash with transitive clustering.

    Uses union-find so that if A~B and B~C, all three stay grouped even
    if A and C aren't directly similar enough.
    """
    # Compute pHash for candidates that don't have one
    for item in candidates:
        if not item.phash and item.path and item.path.exists():
            ph = phash_image(item.path)
            if ph:
                cache.update_hash(item.uuid, "phash", ph)
                item.phash = ph
        # Dual-scale: compute small pHash
        if not item.phash_small and item.path and item.path.exists():
            ph_small = phash_image_small(item.path)
            if ph_small:
                cache.update_hash(item.uuid, "phash_small", ph_small)
                item.phash_small = ph_small

    # Filter to candidates with valid pHash
    hashed = [item for item in candidates if item.phash]
    if len(hashed) < 2:
        if not hashed:
            logger.debug(
                "Dropping entire cluster (%d items): no pHash available",
                len(candidates),
            )
        return hashed

    dropped = len(candidates) - len(hashed)
    if dropped:
        logger.debug(
            "Dropped %d item(s) from near-duplicate cluster: pHash unavailable",
            dropped,
        )

    # Union-find over pHash distances for transitive clustering
    n = len(hashed)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    hash_ints = [hash_hex_to_int(item.phash) for item in hashed]
    small_hash_ints = [
        hash_hex_to_int(item.phash_small) if item.phash_small else None
        for item in hashed
    ]
    threshold = config.phash_threshold
    small_threshold = config.phash_small_threshold

    for i in range(n):
        for j in range(i + 1, n):
            # Resolution-adaptive threshold
            adaptive_thresh = _adaptive_threshold(
                hashed[i], hashed[j], threshold, config,
            )
            matched = hamming_distance_int(hash_ints[i], hash_ints[j]) <= adaptive_thresh
            # Dual-scale fallback
            if not matched and small_hash_ints[i] is not None and small_hash_ints[j] is not None:
                matched = hamming_distance_int(small_hash_ints[i], small_hash_ints[j]) <= small_threshold
            if matched:
                _union(i, j)

    # Find the largest cluster
    cluster_map: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        cluster_map[_find(i)].append(i)

    largest = max(cluster_map.values(), key=len)
    if len(largest) < 2:
        return []

    return [hashed[i] for i in largest]


def find_video_duplicates(
    cache: CacheDB,
    config: Config,
    progress_callback: Callable[[int, int], None] | None = None,
    include_near: bool = False,
    sha_progress_callback: Callable[[int, int], None] | None = None,
    keyframe_progress_callback: Callable[[int, int], None] | None = None,
    compare_progress_callback: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """Find duplicate videos: group by duration, then SHA-256/file-size, then keyframe hashing.

    When *include_near* is False (default), only exact matches (SHA-256 /
    file-size) are returned.  Set to True to also run ffmpeg keyframe
    dHash comparison on remaining unmatched items.
    """
    exact_groups: list[DuplicateGroup] = []
    near_groups: list[DuplicateGroup] = []
    max_workers = _default_max_workers(config)

    duration_groups = cache.get_duration_groups(
        tolerance=config.video_duration_tolerance
    )

    total = sum(len(g) for g in duration_groups)
    logger.debug(
        "Video duplicates: %d duration groups, %d total items",
        len(duration_groups), total,
    )

    # Phase 1: Collect all items needing SHA-256 across all duration groups
    needs_sha: list[tuple[str, str]] = []
    items_by_uuid: dict[str, MediaItem] = {}
    for group_items in duration_groups:
        for item in group_items:
            items_by_uuid[item.uuid] = item
            if item.path and item.path.exists() and not item.sha256:
                needs_sha.append((item.uuid, str(item.path)))

    # Phase 2: Compute SHA-256 hashes in parallel (use sha256_video for videos)
    new_hashes = compute_hashes_parallel(
        work_items=needs_sha,
        hash_fn=sha256_video,
        hash_type="sha256",
        cache=cache,
        max_workers=max_workers,
        progress_callback=sha_progress_callback or progress_callback,
    )

    # Phase 3: Group by SHA-256 within each duration group
    all_unmatched: list[MediaItem] = []
    processed = len(needs_sha)

    for group_idx, group_items in enumerate(duration_groups):
        sha_map: dict[str, list[MediaItem]] = defaultdict(list)
        size_map: dict[int, list[MediaItem]] = defaultdict(list)
        unmatched: list[MediaItem] = []

        for item in group_items:
            if not item.path or not item.path.exists():
                if item.file_size > 0:
                    size_map[item.file_size].append(item)
                processed += 1
                if progress_callback:
                    progress_callback(processed, total)
                continue

            sha = item.sha256 or new_hashes.get(item.uuid)
            if sha:
                sha_map[sha].append(item)
            else:
                unmatched.append(item)

            if item.sha256:
                processed += 1
                if progress_callback:
                    progress_callback(processed, total)

        # Merge cloud-only items into SHA groups by file_size
        for size, cloud_items in size_map.items():
            matching_shas = [
                sha for sha, sha_items in sha_map.items()
                if sha_items[0].file_size == size
            ]
            if len(matching_shas) == 1:
                sha_map[matching_shas[0]].extend(cloud_items)
            elif not matching_shas and len(cloud_items) >= 2:
                exact_groups.append(
                    DuplicateGroup(
                        group_id=0,
                        match_type=MatchType.EXACT,
                        items=cloud_items,
                    )
                )

        for sha, items in sha_map.items():
            if len(items) >= 2:
                exact_groups.append(
                    DuplicateGroup(
                        group_id=0,
                        match_type=MatchType.EXACT,
                        items=items,
                    )
                )
            else:
                unmatched.extend(items)

        all_unmatched.extend(unmatched)

    # Phase 4: Keyframe dHash for near matches (parallel)
    if include_near and len(all_unmatched) >= 2:
        logger.debug(
            "Near-duplicate stage: %d unmatched video items", len(all_unmatched),
        )
        keyframe_work = [
            (item.uuid, str(item.path))
            for item in all_unmatched
            if item.path and item.path.exists()
        ]
        frame_hashes = compute_video_hashes_parallel(
            work_items=keyframe_work,
            max_workers=max_workers,
            progress_callback=keyframe_progress_callback,
        )

        visited: set[str] = set()
        n_unmatched = len(all_unmatched)
        for i, item_a in enumerate(all_unmatched):
            if compare_progress_callback:
                compare_progress_callback(i + 1, n_unmatched)
            if item_a.uuid in visited or item_a.uuid not in frame_hashes:
                continue
            cluster = [item_a]
            for item_b in all_unmatched[i + 1:]:
                if item_b.uuid in visited or item_b.uuid not in frame_hashes:
                    continue
                if video_frames_similar(
                    frame_hashes[item_a.uuid],
                    frame_hashes[item_b.uuid],
                    threshold=config.dhash_threshold,
                ):
                    cluster.append(item_b)
                    visited.add(item_b.uuid)
            if len(cluster) >= 2:
                visited.add(item_a.uuid)
                near_groups.append(
                    DuplicateGroup(
                        group_id=0,
                        match_type=MatchType.NEAR,
                        items=cluster,
                    )
                )

    return exact_groups + near_groups


def _probe_duration(video_path: Path) -> float | None:
    """Get video duration via ffprobe. Fallback for live photos without cached duration."""
    import json
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(video_path),
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            duration = info.get("format", {}).get("duration")
            if duration:
                return float(duration)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, KeyError):
        pass
    return None


def find_live_photo_video_duplicates(
    cache: CacheDB,
    config: Config,
    progress_callback: Callable[[int, int], None] | None = None,
    include_near: bool = False,
    min_duration: float | None = None,
    max_duration: float | None = None,
    sha_progress_callback: Callable[[int, int], None] | None = None,
    keyframe_progress_callback: Callable[[int, int], None] | None = None,
    match_progress_callback: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """Find duplicates between live photo video components and standalone videos.

    For each live photo with a video path, compares its .mov component against
    standalone videos with similar duration. Uses SHA-256 for exact matches
    and keyframe dHash (when include_near=True) for near matches.

    When min_duration/max_duration are set, only standalone videos within that
    duration range are considered (useful for targeting short clips that match
    live photo lengths, typically 2-5 seconds).
    """
    exact_groups: list[DuplicateGroup] = []
    near_groups: list[DuplicateGroup] = []
    max_workers = _default_max_workers(config)

    live_photos = cache.get_live_photos_with_video()
    if not live_photos:
        return []

    all_items = cache.get_all_items()
    standalone_videos = [
        i for i in all_items
        if i.media_type == MediaType.VIDEO and i.duration is not None
        and (min_duration is None or i.duration >= min_duration)
        and (max_duration is None or i.duration <= max_duration)
    ]
    if not standalone_videos:
        return []

    tolerance = config.video_duration_tolerance
    logger.debug(
        "Live photo vs video: %d live photos, %d standalone videos",
        len(live_photos), len(standalone_videos),
    )

    # Pre-hash: SHA-256 for all live photo video paths and unhashed standalone videos
    lp_sha_work = [
        (lp.uuid, str(lp.live_photo_video_path))
        for lp in live_photos
        if lp.live_photo_video_path and lp.live_photo_video_path.exists()
    ]
    vid_sha_work = [
        (v.uuid, str(v.path))
        for v in standalone_videos
        if not v.sha256 and v.path and v.path.exists()
    ]

    # Pre-hash keyframes work lists (needed for total calculation)
    lp_keyframe_work: list[tuple[str, str]] = []
    vid_keyframe_work: list[tuple[str, str]] = []
    if include_near:
        lp_keyframe_work = [
            (lp.uuid, str(lp.live_photo_video_path))
            for lp in live_photos
            if lp.live_photo_video_path and lp.live_photo_video_path.exists()
        ]
        vid_keyframe_work = [
            (v.uuid, str(v.path))
            for v in standalone_videos
            if v.path and v.path.exists()
        ]

    # SHA-256 progress: combine live photo + video SHA work into one callback
    sha_total = len(lp_sha_work) + len(vid_sha_work)
    sha_done = [0]

    def _sha_cb(done: int, total: int) -> None:
        cb = sha_progress_callback or progress_callback
        if cb:
            cb(sha_done[0] + done, sha_total)

    lp_sha_results = compute_hashes_parallel(
        work_items=lp_sha_work,
        hash_fn=sha256_file,
        hash_type="sha256",
        cache=cache,
        max_workers=max_workers,
        progress_callback=_sha_cb,
    )
    sha_done[0] += len(lp_sha_work)

    vid_sha_results = compute_hashes_parallel(
        work_items=vid_sha_work,
        hash_fn=sha256_file,
        hash_type="sha256",
        cache=cache,
        max_workers=max_workers,
        progress_callback=_sha_cb,
    )

    # Build lookup of video SHA → items
    vid_sha_lookup: dict[str, list[MediaItem]] = defaultdict(list)
    for v in standalone_videos:
        sha = v.sha256 or vid_sha_results.get(v.uuid)
        if sha:
            vid_sha_lookup[sha].append(v)

    # Pre-hash keyframes if needed
    lp_frame_hashes: dict[str, list[str]] = {}
    vid_frame_hashes: dict[str, list[str]] = {}
    if include_near:
        keyframe_total = len(lp_keyframe_work) + len(vid_keyframe_work)
        keyframe_done = [0]

        def _kf_cb(done: int, total: int) -> None:
            cb = keyframe_progress_callback or progress_callback
            if cb:
                cb(keyframe_done[0] + done, keyframe_total)

        lp_frame_hashes = compute_video_hashes_parallel(
            work_items=lp_keyframe_work,
            max_workers=max_workers,
            progress_callback=_kf_cb,
        )
        keyframe_done[0] += len(lp_keyframe_work)

        vid_frame_hashes = compute_video_hashes_parallel(
            work_items=vid_keyframe_work,
            max_workers=max_workers,
            progress_callback=_kf_cb,
        )

    # Match live photos against videos using pre-computed hashes
    n_live = len(live_photos)
    for idx, lp in enumerate(live_photos):
        _match_cb = match_progress_callback or progress_callback
        if not lp.live_photo_video_path or not lp.live_photo_video_path.exists():
            if _match_cb:
                _match_cb(idx + 1, n_live)
            continue

        # Get live photo video duration (from cache or probe)
        lp_duration = lp.duration
        if lp_duration is None:
            lp_duration = _probe_duration(lp.live_photo_video_path)
        if lp_duration is None:
            if _match_cb:
                _match_cb(idx + 1, n_live)
            continue

        # Find standalone videos with similar duration
        candidates = [
            v for v in standalone_videos
            if abs(v.duration - lp_duration) <= tolerance
        ]
        if not candidates:
            if _match_cb:
                _match_cb(idx + 1, n_live)
            continue

        # Stage 1: SHA-256 comparison using pre-computed hashes
        lp_sha = lp_sha_results.get(lp.uuid)
        matched_exact = False
        if lp_sha:
            sha_matches = [v for v in candidates if v.uuid in vid_sha_lookup.get(lp_sha, [])]
            if not sha_matches:
                # Check via lookup
                for v in candidates:
                    v_sha = v.sha256 or vid_sha_results.get(v.uuid)
                    if v_sha == lp_sha:
                        sha_matches.append(v)

            if sha_matches:
                exact_groups.append(
                    DuplicateGroup(
                        group_id=0,
                        match_type=MatchType.EXACT,
                        items=[lp] + sha_matches,
                    )
                )
                matched_exact = True

        # Stage 2: Keyframe dHash using pre-computed hashes
        if include_near and not matched_exact and lp.uuid in lp_frame_hashes:
            for vid in candidates:
                if vid.uuid in vid_frame_hashes and video_frames_similar(
                    lp_frame_hashes[lp.uuid],
                    vid_frame_hashes[vid.uuid],
                    threshold=config.dhash_threshold,
                ):
                    near_groups.append(
                        DuplicateGroup(
                            group_id=0,
                            match_type=MatchType.NEAR,
                            items=[lp, vid],
                        )
                    )

        if _match_cb:
            _match_cb(idx + 1, n_live)

    return exact_groups + near_groups


# UTIs considered HEIC and JPEG for cross-format detection
_HEIC_UTIS = {"public.heic", "public.heif"}
_JPEG_UTIS = {"public.jpeg"}


def find_heic_jpeg_duplicates(
    cache: CacheDB,
    config: Config,
    progress_callback: Callable[[int, int], None] | None = None,
    compare_progress_callback: Callable[[int, int], None] | None = None,
    exclude_uuids: set[str] | None = None,
) -> list[DuplicateGroup]:
    """Find duplicates between HEIC and JPEG versions of the same photo.

    Compares HEIC photos against JPEG photos using perceptual hashing (dHash
    with pHash confirmation). Creates cross-format groups where the same image
    exists in both formats. HEIC is preferred as the keeper via quality scoring.

    Pass *exclude_uuids* to skip items already in other duplicate groups.
    """
    _excluded = exclude_uuids or set()
    max_workers = _default_max_workers(config)

    all_items = cache.get_all_items()
    heic_items = [
        i for i in all_items
        if i.uti in _HEIC_UTIS
        and i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
        and i.uuid not in _excluded
    ]
    jpeg_items = [
        i for i in all_items
        if i.uti in _JPEG_UTIS
        and i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
        and i.uuid not in _excluded
    ]

    if not heic_items or not jpeg_items:
        return []

    logger.debug(
        "HEIC vs JPEG: %d HEIC items, %d JPEG items",
        len(heic_items), len(jpeg_items),
    )

    # Compute dHash for items that need it
    needs_dhash: list[tuple[str, str]] = []
    for item in heic_items + jpeg_items:
        if not item.dhash:
            needs_dhash.append((item.uuid, str(item.path)))

    new_dhashes = compute_hashes_parallel(
        work_items=needs_dhash,
        hash_fn=dhash_image,
        hash_type="dhash",
        cache=cache,
        max_workers=max_workers,
        progress_callback=progress_callback,
    )

    # Merge new hashes back
    for item in heic_items + jpeg_items:
        if not item.dhash and item.uuid in new_dhashes:
            item.dhash = new_dhashes[item.uuid]

    # Dual-scale: compute small dHash for all items
    needs_dhash_small: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in heic_items + jpeg_items
        if not item.dhash_small and item.path
    ]
    if needs_dhash_small:
        new_small = compute_hashes_parallel(
            work_items=needs_dhash_small,
            hash_fn=dhash_image_small,
            hash_type="dhash_small",
            cache=cache,
            max_workers=max_workers,
        )
        for item in heic_items + jpeg_items:
            if not item.dhash_small and item.uuid in new_small:
                item.dhash_small = new_small[item.uuid]

    # Filter to items with valid dHash and pre-convert to ints
    hashed_heic = [i for i in heic_items if i.dhash]
    hashed_jpeg = [i for i in jpeg_items if i.dhash]
    if not hashed_heic or not hashed_jpeg:
        return []

    heic_ints = [hash_hex_to_int(i.dhash) for i in hashed_heic]
    jpeg_ints = [hash_hex_to_int(i.dhash) for i in hashed_jpeg]
    heic_small_ints = [
        hash_hex_to_int(i.dhash_small) if i.dhash_small else None
        for i in hashed_heic
    ]
    jpeg_small_ints = [
        hash_hex_to_int(i.dhash_small) if i.dhash_small else None
        for i in hashed_jpeg
    ]
    threshold = config.dhash_threshold
    small_threshold = config.dhash_small_threshold

    # Match each HEIC item against JPEG items by dHash similarity
    # First pass: collect all dHash-matched candidates
    heic_to_jpeg_matches: list[tuple[int, list[int]]] = []
    matched_jpeg_indices: set[int] = set()

    for idx, heic in enumerate(hashed_heic):
        if compare_progress_callback:
            compare_progress_callback(idx + 1, len(hashed_heic))

        ha = heic_ints[idx]
        ha_small = heic_small_ints[idx]
        jpeg_match_indices: list[int] = []
        for j, jpeg in enumerate(hashed_jpeg):
            if j in matched_jpeg_indices:
                continue
            adaptive_thresh = _adaptive_threshold(heic, jpeg, threshold, config)
            matched = hamming_distance_int(ha, jpeg_ints[j]) <= adaptive_thresh
            if not matched and ha_small is not None and jpeg_small_ints[j] is not None:
                matched = hamming_distance_int(ha_small, jpeg_small_ints[j]) <= small_threshold
            if matched:
                jpeg_match_indices.append(j)

        if jpeg_match_indices:
            heic_to_jpeg_matches.append((idx, jpeg_match_indices))

    # Batch-compute pHash for all items involved in dHash matches
    candidate_items: dict[str, MediaItem] = {}
    for heic_idx, jpeg_indices in heic_to_jpeg_matches:
        candidate_items[hashed_heic[heic_idx].uuid] = hashed_heic[heic_idx]
        for j in jpeg_indices:
            candidate_items[hashed_jpeg[j].uuid] = hashed_jpeg[j]

    needs_phash: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in candidate_items.values()
        if not item.phash and item.path and item.path.exists()
    ]
    if needs_phash:
        new_phashes = compute_hashes_parallel(
            work_items=needs_phash,
            hash_fn=phash_image,
            hash_type="phash",
            cache=cache,
            max_workers=max_workers,
        )
        for uuid, ph in new_phashes.items():
            if uuid in candidate_items:
                candidate_items[uuid].phash = ph

    # Second pass: pHash confirmation
    groups: list[DuplicateGroup] = []
    matched_jpeg_uuids: set[str] = set()

    for heic_idx, jpeg_indices in heic_to_jpeg_matches:
        heic = hashed_heic[heic_idx]
        jpeg_matches = [
            hashed_jpeg[j] for j in jpeg_indices
            if hashed_jpeg[j].uuid not in matched_jpeg_uuids
        ]
        if not jpeg_matches:
            continue

        cluster = [heic] + jpeg_matches
        confirmed = _confirm_with_phash(cluster, cache, config)
        if len(confirmed) >= 2:
            has_heic = any(i.uti in _HEIC_UTIS for i in confirmed)
            has_jpeg = any(i.uti in _JPEG_UTIS for i in confirmed)
            if has_heic and has_jpeg:
                groups.append(
                    DuplicateGroup(
                        group_id=0,
                        match_type=MatchType.NEAR,
                        items=confirmed,
                    )
                )
                for item in confirmed:
                    if item.uti in _JPEG_UTIS:
                        matched_jpeg_uuids.add(item.uuid)

    return groups


def _compute_noise_scores(
    items: list[MediaItem],
    max_workers: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, float]:
    """Compute noise levels for items in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    work = [(item.uuid, item.path) for item in items if item.path and item.path.exists()]
    total = len(work)
    if total == 0:
        return {}

    results: dict[str, float] = {}
    done = 0

    if max_workers <= 1 or total <= 1:
        for uuid, path in work:
            score = noise_level(path)
            if score is not None:
                results[uuid] = score
            done += 1
            if progress_callback:
                progress_callback(done, total)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_uuid = {
            executor.submit(noise_level, path): uuid
            for uuid, path in work
        }
        for future in as_completed(future_to_uuid):
            uuid = future_to_uuid[future]
            done += 1
            try:
                score = future.result()
                if score is not None:
                    results[uuid] = score
            except Exception:
                logger.debug("Noise computation failed for %s", uuid, exc_info=True)
            if progress_callback:
                progress_callback(done, total)

    return results


def find_grainy_duplicates(
    cache: CacheDB,
    config: Config,
    noise_progress_callback: Callable[[int, int], None] | None = None,
    dhash_progress_callback: Callable[[int, int], None] | None = None,
    match_progress_callback: Callable[[int, int], None] | None = None,
    exclude_uuids: set[str] | None = None,
) -> list[DuplicateGroup]:
    """Find grainy/noisy photos that have a clearer version in the library.

    For each photo with high noise, finds the closest perceptually-similar
    photo with significantly lower noise.  Each grainy photo is matched to
    at most one clear counterpart, avoiding false-positive multi-grouping.
    """
    _excluded = exclude_uuids or set()
    max_workers = _default_max_workers(config)

    all_items = cache.get_all_items()
    photos = [
        i for i in all_items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
        and i.uuid not in _excluded
    ]

    if len(photos) < 2:
        return []

    # Step 1: Compute noise scores for all photos
    noise_scores = _compute_noise_scores(photos, max_workers, noise_progress_callback)

    if len(noise_scores) < 2:
        return []

    # Step 2: Classify grainy vs clear using median-relative threshold
    import numpy as np
    all_scores = list(noise_scores.values())
    median_noise = float(np.median(all_scores))
    grainy_threshold = median_noise * config.noise_ratio

    grainy_photos = [
        p for p in photos
        if noise_scores.get(p.uuid, 0) > grainy_threshold
    ]
    clear_photos = [
        p for p in photos
        if p.uuid in noise_scores and noise_scores[p.uuid] <= grainy_threshold
    ]

    if not grainy_photos or not clear_photos:
        return []

    logger.debug(
        "Grainy finder: %d grainy, %d clear (threshold=%.1f, median=%.1f)",
        len(grainy_photos), len(clear_photos), grainy_threshold, median_noise,
    )

    # Step 3: Ensure dHash is computed for all photos
    needs_dhash: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in photos
        if not item.dhash
    ]
    if needs_dhash:
        new_dhashes = compute_hashes_parallel(
            work_items=needs_dhash,
            hash_fn=dhash_image,
            hash_type="dhash",
            cache=cache,
            max_workers=max_workers,
            progress_callback=dhash_progress_callback,
        )
        for item in photos:
            if not item.dhash and item.uuid in new_dhashes:
                item.dhash = new_dhashes[item.uuid]

    # Dual-scale: compute small dHash
    needs_dhash_small: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in photos
        if not item.dhash_small and item.path
    ]
    if needs_dhash_small:
        new_small = compute_hashes_parallel(
            work_items=needs_dhash_small,
            hash_fn=dhash_image_small,
            hash_type="dhash_small",
            cache=cache,
            max_workers=max_workers,
        )
        for item in photos:
            if not item.dhash_small and item.uuid in new_small:
                item.dhash_small = new_small[item.uuid]

    # Step 4: For each grainy photo, find best clear match by dHash
    hashed_clear = [p for p in clear_photos if p.dhash]
    if not hashed_clear:
        return []

    clear_ints = [hash_hex_to_int(p.dhash) for p in hashed_clear]
    clear_small_ints = [
        hash_hex_to_int(p.dhash_small) if p.dhash_small else None
        for p in hashed_clear
    ]
    clear_by_uuid = {p.uuid: p for p in hashed_clear}
    threshold = config.dhash_threshold
    small_threshold = config.dhash_small_threshold
    noise_ratio = config.noise_ratio

    # Map clear_uuid → list of grainy items matched to it
    matches: dict[str, list[MediaItem]] = defaultdict(list)

    hashed_grainy = [p for p in grainy_photos if p.dhash]
    n_grainy = len(hashed_grainy)

    for idx, grainy in enumerate(hashed_grainy):
        if match_progress_callback:
            match_progress_callback(idx + 1, n_grainy)

        grainy_hash = hash_hex_to_int(grainy.dhash)
        grainy_small = hash_hex_to_int(grainy.dhash_small) if grainy.dhash_small else None
        grainy_noise = noise_scores[grainy.uuid]

        best_dist = threshold + 1
        best_clear_uuid: str | None = None

        for j, clear in enumerate(hashed_clear):
            # Resolution-adaptive threshold
            adaptive_thresh = _adaptive_threshold(grainy, clear, threshold, config)
            dist = hamming_distance_int(grainy_hash, clear_ints[j])
            within_threshold = dist <= adaptive_thresh
            # Dual-scale fallback
            if not within_threshold and grainy_small is not None and clear_small_ints[j] is not None:
                small_dist = hamming_distance_int(grainy_small, clear_small_ints[j])
                within_threshold = small_dist <= small_threshold
                if within_threshold:
                    dist = small_dist  # use small dist for best-match selection
            if within_threshold and dist < best_dist:
                clear_noise = noise_scores.get(clear.uuid, float("inf"))
                if clear_noise > 0 and grainy_noise / clear_noise >= noise_ratio:
                    best_dist = dist
                    best_clear_uuid = clear.uuid

        if best_clear_uuid is not None:
            matches[best_clear_uuid].append(grainy)

    if not matches:
        return []

    # Step 5: Batch-compute pHash for all matched items
    all_match_items: dict[str, MediaItem] = {}
    for clear_uuid, grainy_items in matches.items():
        all_match_items[clear_uuid] = clear_by_uuid[clear_uuid]
        for item in grainy_items:
            all_match_items[item.uuid] = item

    needs_phash: list[tuple[str, str]] = [
        (item.uuid, str(item.path))
        for item in all_match_items.values()
        if not item.phash and item.path and item.path.exists()
    ]
    if needs_phash:
        new_phashes = compute_hashes_parallel(
            work_items=needs_phash,
            hash_fn=phash_image,
            hash_type="phash",
            cache=cache,
            max_workers=max_workers,
        )
        for uuid, ph in new_phashes.items():
            if uuid in all_match_items:
                all_match_items[uuid].phash = ph

    # Step 6: pHash confirmation and group creation
    groups: list[DuplicateGroup] = []
    for clear_uuid, grainy_items in matches.items():
        clear_item = clear_by_uuid[clear_uuid]
        cluster = [clear_item] + grainy_items
        confirmed = _confirm_with_phash(cluster, cache, config)
        if len(confirmed) >= 2:
            groups.append(
                DuplicateGroup(
                    group_id=0,
                    match_type=MatchType.NEAR,
                    items=confirmed,
                )
            )

    return groups
