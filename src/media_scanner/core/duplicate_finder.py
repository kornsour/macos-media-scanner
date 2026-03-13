"""Multi-stage duplicate detection pipeline."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from media_scanner.core.hasher import (
    dhash_image,
    hamming_distance,
    hamming_distance_int,
    hash_hex_to_int,
    phash_image,
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
) -> list[DuplicateGroup]:
    """Stage 3+4: dHash grouping with pHash confirmation for photos.

    Operates on photos that weren't caught as exact duplicates.
    """
    # Get all photos that have a path
    all_items = cache.get_all_items()
    photos = [
        i for i in all_items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
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

    # Group by similar dHash — pre-convert to ints for fast comparison
    hashed_photos = [p for p in photos if p.dhash]
    hash_ints = [hash_hex_to_int(p.dhash) for p in hashed_photos]
    n = len(hashed_photos)
    threshold = config.dhash_threshold

    visited: set[str] = set()
    groups: list[DuplicateGroup] = []

    for i, item_a in enumerate(hashed_photos):
        if compare_progress_callback:
            compare_progress_callback(i + 1, n)
        if item_a.uuid in visited:
            continue
        cluster = [item_a]
        ha = hash_ints[i]
        for j in range(i + 1, n):
            item_b = hashed_photos[j]
            if item_b.uuid in visited:
                continue
            if hamming_distance_int(ha, hash_ints[j]) <= threshold:
                cluster.append(item_b)
                visited.add(item_b.uuid)
        if len(cluster) >= 2:
            visited.add(item_a.uuid)
            # Stage 4: pHash confirmation
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
    """Confirm near-duplicates using pHash. Returns confirmed group."""
    # Compute pHash for candidates that don't have one
    for item in candidates:
        if not item.phash and item.path and item.path.exists():
            ph = phash_image(item.path)
            if ph:
                cache.update_hash(item.uuid, "phash", ph)
                item.phash = ph

    # Keep items whose pHash is close to the first item's pHash
    anchor = candidates[0]
    if not anchor.phash:
        return candidates  # Can't confirm, return all

    confirmed = [anchor]
    for item in candidates[1:]:
        if not item.phash:
            confirmed.append(item)  # Can't confirm, keep it
            continue
        dist = hamming_distance(anchor.phash, item.phash)
        if dist <= config.phash_threshold:
            confirmed.append(item)

    return confirmed


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
