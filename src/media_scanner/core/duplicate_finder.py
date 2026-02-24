"""Multi-stage duplicate detection pipeline."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from media_scanner.core.hasher import dhash_image, hamming_distance, phash_image, sha256_file
from media_scanner.core.video_hasher import dhash_video, sha256_video, video_frames_similar
from media_scanner.data.models import DuplicateGroup, MatchType, MediaItem, MediaType

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_scanner.config import Config
    from media_scanner.data.cache import CacheDB


def find_exact_duplicates(
    cache: CacheDB,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """Stage 1+2: Group by file size, then SHA-256 within groups.

    Only computes hashes for files that share a size with another file.
    """
    groups: list[DuplicateGroup] = []
    size_groups = cache.get_size_groups(min_group_size=2)

    total_items = sum(len(uuids) for uuids in size_groups.values())
    processed = 0

    for file_size, uuids in size_groups.items():
        # Compute SHA-256 for each item in the size group
        sha_map: dict[str, list[MediaItem]] = defaultdict(list)
        for uuid in uuids:
            item = cache.get_item(uuid)
            if not item or not item.path or not item.path.exists():
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_items)
                continue

            # Use cached hash if available
            if item.sha256:
                sha = item.sha256
            else:
                sha = sha256_file(item.path)
                if sha:
                    cache.update_hash(uuid, "sha256", sha)

            if sha:
                sha_map[sha].append(item)

            processed += 1
            if progress_callback:
                progress_callback(processed, total_items)

        # Create duplicate groups for matching SHA-256s
        for sha, items in sha_map.items():
            if len(items) >= 2:
                group = DuplicateGroup(
                    group_id=0,
                    match_type=MatchType.EXACT,
                    items=items,
                )
                groups.append(group)

    return groups


def find_near_duplicates(
    cache: CacheDB,
    config: Config,
    progress_callback: Callable[[int, int], None] | None = None,
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

    total = len(photos)

    # Compute dHash for all photos that don't have one yet
    for idx, item in enumerate(photos):
        if not item.dhash:
            dh = dhash_image(item.path)
            if dh:
                cache.update_hash(item.uuid, "dhash", dh)
                item.dhash = dh
        if progress_callback:
            progress_callback(idx + 1, total)

    # Group by similar dHash (quadratic, but with early termination)
    hashed_photos = [p for p in photos if p.dhash]
    visited: set[str] = set()
    groups: list[DuplicateGroup] = []

    for i, item_a in enumerate(hashed_photos):
        if item_a.uuid in visited:
            continue
        cluster = [item_a]
        for item_b in hashed_photos[i + 1:]:
            if item_b.uuid in visited:
                continue
            dist = hamming_distance(item_a.dhash, item_b.dhash)
            if dist <= config.dhash_threshold:
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
) -> list[DuplicateGroup]:
    """Find duplicate videos: group by duration, then SHA-256, then keyframe hashing."""
    exact_groups: list[DuplicateGroup] = []
    near_groups: list[DuplicateGroup] = []

    duration_groups = cache.get_duration_groups(
        tolerance=config.video_duration_tolerance
    )

    total = sum(len(g) for g in duration_groups)
    processed = 0

    for group_items in duration_groups:
        # Stage 2: SHA-256 for exact matches
        sha_map: dict[str, list[MediaItem]] = defaultdict(list)
        unmatched: list[MediaItem] = []

        for item in group_items:
            if not item.path or not item.path.exists():
                processed += 1
                if progress_callback:
                    progress_callback(processed, total)
                continue

            if item.sha256:
                sha = item.sha256
            else:
                sha = sha256_video(item.path)
                if sha:
                    cache.update_hash(item.uuid, "sha256", sha)

            if sha:
                sha_map[sha].append(item)
            else:
                unmatched.append(item)

            processed += 1
            if progress_callback:
                progress_callback(processed, total)

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

        # Stage 3: Keyframe dHash for near matches
        if len(unmatched) >= 2:
            frame_hashes: dict[str, list[str]] = {}
            for item in unmatched:
                if item.path and item.path.exists():
                    fh = dhash_video(item.path)
                    if fh:
                        frame_hashes[item.uuid] = fh

            visited: set[str] = set()
            for i, item_a in enumerate(unmatched):
                if item_a.uuid in visited or item_a.uuid not in frame_hashes:
                    continue
                cluster = [item_a]
                for item_b in unmatched[i + 1:]:
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
