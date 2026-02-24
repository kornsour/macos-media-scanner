"""Find visually similar (but not duplicate) photos."""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_scanner.core.hasher import dhash_image, hamming_distance, phash_image
from media_scanner.data.models import DuplicateGroup, MatchType, MediaItem, MediaType

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_scanner.config import Config
    from media_scanner.data.cache import CacheDB


def find_similar_photos(
    cache: CacheDB,
    config: Config,
    min_distance: int | None = None,
    max_distance: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    """Find visually similar photos with a wider threshold than near-duplicates.

    Similar means: dhash distance between (dhash_threshold, max_distance].
    This catches photos taken seconds apart, same scene different angle, etc.
    """
    # Defaults: similar range is wider than dupe range
    min_dist = min_distance if min_distance is not None else config.dhash_threshold + 1
    max_dist = max_distance if max_distance is not None else config.dhash_threshold * 3

    items = cache.get_all_items()
    photos = [
        i for i in items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.path and i.path.exists()
    ]

    total = len(photos)

    # Ensure all photos have dHash
    for idx, item in enumerate(photos):
        if not item.dhash:
            dh = dhash_image(item.path)
            if dh:
                cache.update_hash(item.uuid, "dhash", dh)
                item.dhash = dh
        if progress_callback:
            progress_callback(idx + 1, total)

    hashed = [p for p in photos if p.dhash]
    visited: set[str] = set()
    groups: list[DuplicateGroup] = []

    for i, item_a in enumerate(hashed):
        if item_a.uuid in visited:
            continue
        cluster = [item_a]
        for item_b in hashed[i + 1:]:
            if item_b.uuid in visited:
                continue
            dist = hamming_distance(item_a.dhash, item_b.dhash)
            if min_dist <= dist <= max_dist:
                cluster.append(item_b)
                visited.add(item_b.uuid)
        if len(cluster) >= 2:
            visited.add(item_a.uuid)
            groups.append(
                DuplicateGroup(
                    group_id=0,
                    match_type=MatchType.SIMILAR,
                    items=cluster,
                )
            )

    return groups
