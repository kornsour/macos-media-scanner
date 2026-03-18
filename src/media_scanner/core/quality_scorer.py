"""Quality scoring for duplicate group ranking."""

from __future__ import annotations

import logging

from media_scanner.config import Config
from media_scanner.data.models import DuplicateGroup, MediaItem, MediaType

logger = logging.getLogger(__name__)

# Higher = better format
FORMAT_SCORES: dict[str, float] = {
    "com.adobe.raw-image": 1.0,
    "public.tiff": 0.95,
    "com.canon.cr2-raw-image": 1.0,
    "com.canon.cr3-raw-image": 1.0,
    "com.nikon.nrw-raw-image": 1.0,
    "com.sony.arw-raw-image": 1.0,
    "com.fuji.raw-image": 1.0,
    "com.apple.proraw": 0.95,
    "public.png": 0.8,
    "public.heic": 0.75,
    "public.heif": 0.75,
    "public.jpeg": 0.6,
    "com.compuserve.gif": 0.3,
    # Video formats
    "com.apple.quicktime-movie": 0.7,
    "public.mpeg-4": 0.65,
    "com.apple.m4v-video": 0.65,
}


def score_item(item: MediaItem, group: DuplicateGroup, config: Config) -> float:
    """Compute a quality score for an item within its duplicate group.

    Returns a float 0.0–1.0 where higher is better.
    """
    weights = config.quality_weights
    score = 0.0

    # ── Resolution (30%) ─────────────────────────────────────
    max_pixels = max(i.width * i.height for i in group.items) or 1
    item_pixels = item.width * item.height
    score += weights["resolution"] * (item_pixels / max_pixels)

    # ── Format (20%) ─────────────────────────────────────────
    fmt_score = FORMAT_SCORES.get(item.uti, 0.5)
    score += weights["format"] * fmt_score

    # ── File size (15%) ──────────────────────────────────────
    max_size = max(i.file_size for i in group.items) or 1
    score += weights["file_size"] * (item.file_size / max_size)

    # ── Metadata completeness (10%) ──────────────────────────
    meta_parts = 0
    meta_total = 4
    if item.has_gps:
        meta_parts += 1
    if item.persons:
        meta_parts += 1
    if item.keywords:
        meta_parts += 1
    if item.albums:
        meta_parts += 1
    score += weights["metadata"] * (meta_parts / meta_total)

    # ── Date originality (10%) ───────────────────────────────
    dates = [i.date_created for i in group.items if i.date_created]
    if dates and item.date_created:
        earliest = min(dates)
        if item.date_created == earliest:
            score += weights["date_originality"] * 1.0
        else:
            # Slight penalty for later dates
            score += weights["date_originality"] * 0.5

    # ── Apple quality score (10%) ────────────────────────────
    if item.apple_score is not None:
        score += weights["apple_score"] * min(item.apple_score, 1.0)
    else:
        score += weights["apple_score"] * 0.5  # neutral

    # ── Edit status (5%) ─────────────────────────────────────
    if item.is_edited:
        score += weights["edit_status"] * 1.0
    else:
        score += weights["edit_status"] * 0.3

    # ── Video bitrate bonus (15%) ────────────────────────────
    # When videos share the same resolution AND similar duration,
    # bitrate (file_size / duration) directly measures compression
    # quality.  Only applied when durations are close — otherwise
    # a longer video would be unfairly penalised for its naturally
    # lower bitrate.
    is_video_group = all(
        i.media_type in (MediaType.VIDEO, MediaType.LIVE_PHOTO)
        for i in group.items
    )
    if is_video_group and item.duration and item.duration > 0:
        durations = [
            i.duration for i in group.items
            if i.duration and i.duration > 0
        ]
        if durations:
            max_duration = max(durations)
            resolutions_tied = len({i.width * i.height for i in group.items}) == 1
            durations_tied = max_duration > 0 and (
                (max_duration - min(durations)) / max_duration < 0.05
            )
            if resolutions_tied and durations_tied:
                bitrates = [
                    i.file_size / i.duration
                    for i in group.items
                    if i.duration and i.duration > 0
                ]
                if bitrates:
                    max_bitrate = max(bitrates)
                    item_bitrate = item.file_size / item.duration
                    score += 0.15 * (item_bitrate / max_bitrate)

    # ── Media type bonus (cross-type groups) ──────────────────
    # When a live photo competes against a standalone video,
    # prefer the live photo (it contains both photo + video).
    has_mixed_types = any(
        i.media_type == MediaType.VIDEO for i in group.items
    ) and any(
        i.media_type == MediaType.LIVE_PHOTO for i in group.items
    )
    if has_mixed_types and item.media_type == MediaType.LIVE_PHOTO:
        score += 0.05

    # ── HEIC format bonus (cross-format groups) ──────────────
    # When HEIC and JPEG versions of the same photo exist,
    # prefer HEIC (better compression, supports HDR/depth data).
    _heic_utis = {"public.heic", "public.heif"}
    _jpeg_utis = {"public.jpeg"}
    group_utis = {i.uti for i in group.items}
    has_mixed_formats = bool(group_utis & _heic_utis) and bool(group_utis & _jpeg_utis)
    if has_mixed_formats and item.uti in _heic_utis:
        score += 0.05

    return round(score, 4)


def _compute_motion_scores(group: DuplicateGroup) -> None:
    """Compute motion scores for video items that don't have one yet.

    Runs ffmpeg frame sampling — only called for video duplicate groups
    during ranking.  Results are stored on the item objects (and should
    be persisted to cache by the caller if desired).

    Items without paths or that already have scores are skipped.
    Remaining items are processed in parallel via a thread pool.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from media_scanner.core.video_hasher import motion_score

    # Identify items that need scoring
    needs_scoring: list[MediaItem] = []
    for item in group.items:
        if item.motion_score is not None:
            continue
        if item.media_type not in (MediaType.VIDEO, MediaType.LIVE_PHOTO):
            continue
        if not item.path or not item.path.exists():
            item.motion_score = 1.0  # can't assess, assume OK
            continue
        needs_scoring.append(item)

    if not needs_scoring:
        return

    def _score_one(item: MediaItem) -> tuple[MediaItem, float]:
        return item, motion_score(item.path)

    with ThreadPoolExecutor(max_workers=min(4, len(needs_scoring))) as pool:
        futures = {pool.submit(_score_one, item): item for item in needs_scoring}
        for future in as_completed(futures):
            try:
                item, score = future.result()
                item.motion_score = score
                logger.debug(
                    "Motion score for %s: %.2f", item.filename, item.motion_score,
                )
            except Exception:
                item = futures[future]
                item.motion_score = 1.0  # assume OK on error
                logger.debug("Motion score failed for %s, defaulting to 1.0", item.filename)


def rank_group(
    group: DuplicateGroup,
    config: Config,
    cache: object | None = None,
) -> DuplicateGroup:
    """Score all items in a group and set the recommended keeper.

    Items are sorted by score descending. The first item is the recommended keeper.

    For video groups:
    - Motion score is the top priority: a video with real motion always
      beats one that freezes partway through.
    - Duration is the next priority: longer video = more content.
    - Quality score is the final tiebreaker.

    If *cache* is provided (a CacheDB instance), newly computed motion
    scores are persisted.
    """
    scored = [(item, score_item(item, group, config)) for item in group.items]

    # Check if this is a video group
    is_video_group = all(
        i.media_type in (MediaType.VIDEO, MediaType.LIVE_PHOTO)
        for i in group.items
    )

    if is_video_group:
        # Compute motion scores for items that need them
        _compute_motion_scores(group)

        # Persist newly computed motion scores to cache
        if cache is not None:
            for item in group.items:
                if item.motion_score is not None:
                    try:
                        cache.update_motion_score(item.uuid, item.motion_score)
                    except Exception:
                        pass

        has_duration_diff = False
        durations = [
            i.duration for i in group.items
            if i.duration and i.duration > 0
        ]
        if len(durations) >= 2:
            has_duration_diff = max(durations) != min(durations)

        # Sort: motion score (highest first) → duration (longest first) → quality score
        scored.sort(
            key=lambda x: (
                x[0].motion_score if x[0].motion_score is not None else 1.0,
                x[0].duration or 0 if has_duration_diff else 0,
                x[1],
            ),
            reverse=True,
        )
    else:
        scored.sort(key=lambda x: x[1], reverse=True)

    group.items = [item for item, _ in scored]
    group.recommended_keep_uuid = group.items[0].uuid
    return group
