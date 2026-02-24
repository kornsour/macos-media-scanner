"""Quality scoring for duplicate group ranking."""

from __future__ import annotations

from media_scanner.config import Config
from media_scanner.data.models import DuplicateGroup, MediaItem

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

    return round(score, 4)


def rank_group(group: DuplicateGroup, config: Config) -> DuplicateGroup:
    """Score all items in a group and set the recommended keeper.

    Items are sorted by score descending. The first item is the recommended keeper.
    """
    scored = [(item, score_item(item, group, config)) for item in group.items]
    scored.sort(key=lambda x: x[1], reverse=True)
    group.items = [item for item, _ in scored]
    group.recommended_keep_uuid = group.items[0].uuid
    return group
