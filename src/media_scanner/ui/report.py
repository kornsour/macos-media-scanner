"""Generate self-contained HTML report for duplicate groups."""

from __future__ import annotations

import base64
import html as html_mod
import io
import json
import logging
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from PIL import Image
from pillow_heif import register_heif_opener

from media_scanner.core.quality_scorer import score_item

register_heif_opener()
from media_scanner.data.models import ActionType, MatchType, MediaType

if TYPE_CHECKING:
    from media_scanner.config import Config
    from media_scanner.data.models import ActionRecord, DuplicateGroup, MediaItem

logger = logging.getLogger(__name__)

THUMB_SIZE = 240
THUMB_QUALITY = 65
PAGE_SIZE = 50

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}


def _video_frame_jpeg(path, thumb_size: int = THUMB_SIZE) -> bytes | None:
    """Extract a single frame from a video using ffmpeg and return JPEG bytes."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-ss", "0.5",
                "-i", str(path),
                "-vframes", "1",
                "-vf", f"scale={thumb_size}:-1",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", "4",
                "-",
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _thumbnail_b64(item: MediaItem) -> str | None:
    """Generate a base64-encoded JPEG thumbnail for an item."""
    if not item.path or not item.path.exists():
        return None
    try:
        suffix = item.path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            data = _video_frame_jpeg(item.path)
            if data:
                return base64.b64encode(data).decode("ascii")
            return None
        with Image.open(item.path) as img:
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        logger.debug("Could not generate thumbnail for %s", item.filename)
        return None


def group_category(group: DuplicateGroup) -> str:
    """Determine a group's primary filter category based on match type and media types.

    Returns one of: exact_photo, near_photo, exact_video, near_video, live_photo, heic_jpeg.

    "live_photo" is only for cross-type groups (mix of LIVE_PHOTO and VIDEO).
    "heic_jpeg" is for cross-format groups (mix of HEIC and JPEG photos).
    Groups where all items are LIVE_PHOTO are treated as photo groups since
    most iPhone photos are technically live photos.
    """
    media_types = {item.media_type for item in group.items}
    # Cross-type: live photo video component duplicated with standalone video
    has_live = MediaType.LIVE_PHOTO in media_types
    has_video = MediaType.VIDEO in media_types
    if has_live and has_video:
        return "live_photo"
    # Cross-format: HEIC + JPEG versions of the same photo
    utis = {item.uti for item in group.items}
    has_heic = bool(utis & {"public.heic", "public.heif"})
    has_jpeg = "public.jpeg" in utis
    if has_heic and has_jpeg:
        return "heic_jpeg"
    match_prefix = "exact" if group.match_type == MatchType.EXACT else "near"
    if media_types == {MediaType.VIDEO}:
        return f"{match_prefix}_video"
    # LIVE_PHOTO-only groups and PHOTO groups are both "photo"
    return f"{match_prefix}_photo"


# Size thresholds for overlapping categories
THUMBNAIL_MAX_BYTES = 500 * 1024      # 500 KB
LARGE_FILE_MIN_BYTES = 50 * 1024 * 1024  # 50 MB


def group_tags(group: DuplicateGroup) -> list[str]:
    """Return all filter tags for a group (overlapping categories).

    Every group gets a match-type tag (exact_photo, near_video, etc.),
    plus "live_photo" for cross-type groups, plus size-based tags.
    Tags can overlap so a group appears in multiple sidebar categories.
    """
    media_types = {item.media_type for item in group.items}
    match_prefix = "exact" if group.match_type == MatchType.EXACT else "near"

    tags = []

    # Match-type tag — always present
    if media_types == {MediaType.VIDEO}:
        tags.append(f"{match_prefix}_video")
    else:
        # PHOTO, LIVE_PHOTO, or mixed photo types all count as "photo"
        tags.append(f"{match_prefix}_photo")

    # Cross-type tag — live photo + standalone video duplicates
    if MediaType.LIVE_PHOTO in media_types and MediaType.VIDEO in media_types:
        tags.append("live_photo")

    # Cross-format tag — HEIC + JPEG versions of the same photo
    utis = {item.uti for item in group.items}
    if bool(utis & {"public.heic", "public.heif"}) and "public.jpeg" in utis:
        tags.append("heic_jpeg")

    sizes = [item.file_size for item in group.items]
    max_size = max(sizes) if sizes else 0
    min_size = min(sizes) if sizes else 0

    # Thumbnails: all items are small
    if max_size <= THUMBNAIL_MAX_BYTES:
        tags.append("small_files")

    # Large files: any item is large
    if max_size >= LARGE_FILE_MIN_BYTES:
        tags.append("large_files")

    # Suspect corrupt: any video item has low motion score
    has_suspect = any(
        item.motion_score is not None and item.motion_score <= 0.25
        for item in group.items
    )
    if has_suspect:
        tags.append("suspect_corrupt")

    return tags


CATEGORY_LABELS = {
    "all": "All",
    "exact_photo": "Exact Photos",
    "near_photo": "Near Photos",
    "exact_video": "Exact Videos",
    "near_video": "Near Videos",
    "live_photo": "Live Photo / Video",
    "heic_jpeg": "HEIC / JPEG",
    "small_files": "Small Files (<500KB)",
    "large_files": "Large Files (>50MB)",
    "suspect_corrupt": "Suspect Corrupt",
}


def _score_pct(item: MediaItem, group: DuplicateGroup, config: Config) -> int:
    return int(round(score_item(item, group, config) * 100))


def _action_for(item: MediaItem, actions: dict[str, ActionRecord]) -> str | None:
    rec = actions.get(item.uuid)
    if not rec:
        return None
    if rec.action == ActionType.KEEP:
        return "keep"
    if rec.action == ActionType.DELETE:
        return "delete"
    return None


def _build_item_card(
    item: MediaItem,
    group: DuplicateGroup,
    config: Config,
    actions: dict[str, ActionRecord],
    interactive: bool,
) -> str:
    """Build HTML for a single item card."""
    score = _score_pct(item, group, config)
    action = _action_for(item, actions)
    is_keeper = item.uuid == group.recommended_keep_uuid

    classes = ["item-card"]
    if is_keeper and interactive:
        classes.append("selected")
    elif is_keeper:
        classes.append("keeper")
    if action == "delete":
        classes.append("marked-delete")
    elif action == "keep":
        classes.append("marked-keep")

    if interactive:
        classes.append("interactive")
    data_attrs = f' data-uuid="{item.uuid}"' if interactive else ""

    # Detect video items
    is_video = item.path and item.path.suffix.lower() in VIDEO_EXTENSIONS

    # Thumbnail — server-served lazy URL in interactive mode, inline b64 in static
    if interactive:
        img_tag = (
            f'<img src="/thumb/{item.uuid}" alt="{html_mod.escape(item.filename)}" loading="lazy">'
        )
        # Add play button overlay for video items
        if is_video:
            img_tag += (
                f'<div class="play-overlay" data-uuid="{item.uuid}" '
                f'onclick="event.stopPropagation(); playVideo(this)">'
                '<svg width="24" height="24" viewBox="0 0 24 24" fill="white">'
                '<polygon points="8,5 20,12 8,19"/></svg></div>'
            )
    else:
        thumb = _thumbnail_b64(item)
        if thumb:
            img_tag = f'<img src="data:image/jpeg;base64,{thumb}" alt="{html_mod.escape(item.filename)}">'
        else:
            img_tag = '<div class="no-thumb">No preview</div>'

    # Badges
    badges = []
    if is_keeper:
        badges.append('<span class="badge badge-keeper">Recommended</span>')
    if action == "keep":
        badges.append('<span class="badge badge-keep">Keep</span>')
    elif action == "delete":
        badges.append('<span class="badge badge-delete">Delete</span>')
    if item.motion_score is not None and item.motion_score <= 0.25:
        if item.motion_score == 0.0:
            badges.append('<span class="badge badge-corrupt">Frozen</span>')
        else:
            badges.append('<span class="badge badge-corrupt">Suspect Corrupt</span>')

    from media_scanner.ui.formatters import format_date, format_duration, format_resolution, format_size

    date_str = format_date(item.date_created)
    size_str = format_size(item.file_size)
    res_str = format_resolution(item.width, item.height)
    duration_str = format_duration(item.duration) if item.duration else None

    meta_items = []
    if item.is_edited:
        meta_items.append("Edited")
    if item.is_favorite:
        meta_items.append("Favorite")
    if item.has_gps:
        meta_items.append("GPS")
    if item.persons:
        meta_items.append(f"{len(item.persons)} people")
    if item.albums:
        meta_items.append(f"{len(item.albums)} albums")
    meta_str = " &middot; ".join(meta_items) if meta_items else ""

    return f"""
    <div class="{' '.join(classes)}"{data_attrs}>
        <div class="thumb-wrap">{img_tag}</div>
        <div class="item-info">
            <div class="item-filename" title="{html_mod.escape(item.filename)}">{html_mod.escape(item.filename)}</div>
            <div class="item-meta">{date_str}</div>
            <div class="item-meta">{size_str} &middot; {res_str}{f' &middot; {duration_str}' if duration_str else ''}{f' &middot; Motion: {int(item.motion_score * 100)}%' if item.motion_score is not None else ''}</div>
            <div class="score-bar-wrap">
                <div class="score-bar" style="width: {score}%"></div>
                <span class="score-label">Quality: {score}%</span>
            </div>
            {f'<div class="item-meta secondary">{meta_str}</div>' if meta_str else ''}
            <div class="badges">{''.join(badges)}</div>
        </div>
    </div>"""


def _build_group_html(
    idx: int,
    group: DuplicateGroup,
    config: Config,
    actions: dict[str, ActionRecord],
    interactive: bool,
) -> str:
    """Build HTML for a duplicate group."""
    cards = [
        _build_item_card(item, group, config, actions, interactive)
        for item in group.items
    ]

    match_badge = (
        '<span class="match-type exact">Exact</span>'
        if group.match_type == MatchType.EXACT
        else '<span class="match-type near">Near</span>'
    )

    tags = group_tags(group)
    tags_str = " ".join(tags)
    data_attr = f' data-group-id="{group.group_id}" data-tags="{tags_str}"' if interactive else ""

    # Merge button for interactive mode
    buttons = ""
    if interactive:
        buttons = f"""
            <div class="group-actions">
                <button class="btn btn-merge" onclick="mergeGroup({group.group_id})">Merge</button>
            </div>"""

    return f"""
    <div class="group"{data_attr}>
        <div class="group-header">
            <span class="group-title">Group {idx}</span>
            {match_badge}
            <span class="group-count">{len(group.items)} items</span>
            {buttons}
        </div>
        <div class="group-items">
            {''.join(cards)}
        </div>
    </div>"""


def generate_report(
    groups: list[DuplicateGroup],
    config: Config,
    actions: dict[str, ActionRecord] | None = None,
    title: str = "Duplicate Report",
    progress_callback: Callable[[int, int], None] | None = None,
    interactive: bool = False,
) -> str:
    """Generate HTML report. Set interactive=True for server-backed merge UI."""
    actions = actions or {}

    total_items = sum(len(g.items) for g in groups)
    total_delete = sum(1 for a in actions.values() if a.action == ActionType.DELETE)
    total_keep = sum(1 for a in actions.values() if a.action == ActionType.KEEP)
    exact_count = sum(1 for g in groups if g.match_type == MatchType.EXACT)
    near_count = sum(1 for g in groups if g.match_type == MatchType.NEAR)

    items_processed = 0
    groups_html = []
    for idx, group in enumerate(groups, 1):
        groups_html.append(
            _build_group_html(idx, group, config, actions, interactive)
        )
        items_processed += len(group.items)
        if progress_callback:
            progress_callback(items_processed, total_items)

    # Stats line
    from media_scanner.ui.formatters import format_count

    stats_parts = [
        f"{format_count(len(groups))} groups",
        f"{format_count(total_items)} total items",
    ]
    if exact_count:
        stats_parts.append(f"{format_count(exact_count)} exact")
    if near_count:
        stats_parts.append(f"{format_count(near_count)} near")
    if total_keep:
        stats_parts.append(f"{format_count(total_keep)} keep")
    if total_delete:
        stats_parts.append(f"{format_count(total_delete)} delete")
    stats_summary = " &middot; ".join(stats_parts)

    # Build keeper map for JS (interactive mode)
    keeper_map_json = ""
    if interactive:
        keeper_map = {
            g.group_id: g.recommended_keep_uuid
            for g in groups
            if g.recommended_keep_uuid
        }
        keeper_map_json = json.dumps(keeper_map)

    sticky_header = ""
    if interactive:
        sticky_header = """
    <div class="sticky-bar" id="sticky-bar">
        <span id="review-count">0 of 0 reviewed</span>
        <span class="sticky-stats" id="sticky-stats"></span>
        <button class="btn btn-merge-all" id="merge-all-btn" onclick="mergeAll()">Merge All</button>
        <div class="size-selector">
            <label for="size-select">Size:</label>
            <select id="size-select" onchange="changeSize(this.value)">
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large" selected>Large</option>
            </select>
        </div>
        <span class="sticky-hint">Click photos to keep (green border). Unselected photos go to delete album.</span>
    </div>"""

    js_block = ""
    if interactive:
        js_block = _interactive_js(keeper_map_json)

    sidebar = ""
    sidebar_css = ""
    layout_open = ""
    layout_close = ""
    if interactive:
        sidebar = _build_sidebar_html(groups)
        sidebar_css = _sidebar_css()
        layout_open = '<div class="layout-wrapper">' + sidebar + '<div class="main-content">'
        layout_close = '</div></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{_css(interactive)}
{sidebar_css}
</style>
</head>
<body>
<div class="header">
    <h1>{title}</h1>
</div>
{sticky_header}
{layout_open}
<div class="stats">{stats_summary}</div>
{''.join(groups_html)}
<div class="footer">Generated by media-scanner</div>
{layout_close}
{js_block}
</body>
</html>"""


def _css(interactive: bool) -> str:
    """Return the full CSS for the report."""
    base = """
:root {
    --bg: #f5f5f7;
    --card-bg: #fff;
    --text: #1d1d1f;
    --text-secondary: #86868b;
    --border: #d2d2d7;
    --keeper-border: #34c759;
    --keeper-bg: #f0faf2;
    --delete-border: #ff3b30;
    --delete-bg: #fef2f1;
    --keep-bg: #f0faf2;
    --exact-bg: #007aff;
    --near-bg: #af52de;
    --score-bar: #34c759;
    --group-bg: #fff;
    --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06);
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #1c1c1e;
        --card-bg: #2c2c2e;
        --text: #f5f5f7;
        --text-secondary: #98989d;
        --border: #48484a;
        --keeper-border: #30d158;
        --keeper-bg: #1a3a1f;
        --delete-border: #ff453a;
        --delete-bg: #3a1a1a;
        --keep-bg: #1a3a1f;
        --group-bg: #2c2c2e;
        --shadow: 0 1px 3px rgba(0,0,0,0.3);
    }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro", "Helvetica Neue", sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
    max-width: 1600px;
    margin: 0 auto;
}
.header {
    text-align: center;
    padding: 32px 0 16px;
}
.header h1 {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.5px;
}
.stats {
    text-align: center;
    color: var(--text-secondary);
    font-size: 14px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
}
.group {
    background: var(--group-bg);
    border-radius: 12px;
    box-shadow: var(--shadow);
    margin-bottom: 20px;
    overflow: hidden;
    transition: opacity 0.3s;
}
.group-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 14px;
}
.group-title { font-weight: 600; }
.group-count {
    color: var(--text-secondary);
    margin-left: auto;
}
.match-type {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    color: #fff;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.match-type.exact { background: var(--exact-bg); }
.match-type.near { background: var(--near-bg); }
.group-items {
    display: flex;
    flex-wrap: wrap;
    padding: 16px;
    gap: 16px;
}
.item-card {
    flex: 0 1 220px;
    border: 2px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    transition: border-color 0.15s, opacity 0.3s, transform 0.15s;
}
.item-card.keeper {
    border-color: var(--keeper-border);
    background: var(--keeper-bg);
}
.item-card.marked-delete {
    border-color: var(--delete-border);
    background: var(--delete-bg);
    opacity: 0.75;
}
.item-card.marked-keep {
    border-color: var(--keeper-border);
    background: var(--keep-bg);
}
.thumb-wrap {
    width: 100%;
    aspect-ratio: 1;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #000;
}
.thumb-wrap img {
    width: 100%;
    height: 100%;
    object-fit: contain;
}
.no-thumb {
    color: var(--text-secondary);
    font-size: 13px;
}
.item-info { padding: 10px; }
.item-filename {
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 4px;
}
.item-meta {
    font-size: 11px;
    color: var(--text-secondary);
    margin-bottom: 2px;
}
.item-meta.secondary { margin-top: 4px; }
.score-bar-wrap {
    position: relative;
    height: 16px;
    background: var(--border);
    border-radius: 8px;
    margin: 6px 0;
    overflow: hidden;
}
.score-bar {
    height: 100%;
    background: var(--score-bar);
    border-radius: 8px;
    transition: width 0.3s;
}
.score-label {
    position: absolute;
    top: 0; left: 6px;
    line-height: 16px;
    font-size: 10px;
    font-weight: 600;
    color: #fff;
    text-shadow: 0 0 3px rgba(0,0,0,0.4);
}
.badges {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    margin-top: 6px;
}
.badge {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 6px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.badge-keeper { background: var(--keeper-border); color: #fff; }
.badge-keep { background: var(--keeper-border); color: #fff; }
.badge-delete { background: var(--delete-border); color: #fff; }
.badge-corrupt { background: #ff9500; color: #fff; }
.thumb-wrap { position: relative; }
.play-overlay {
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    width: 48px; height: 48px;
    background: rgba(0,0,0,0.6);
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10;
    transition: background 0.15s;
}
.play-overlay:hover { background: rgba(0,0,0,0.8); }
.thumb-wrap video {
    width: 100%;
    height: 100%;
    object-fit: contain;
}
.btn-flag-corrupt {
    background: #ff9500;
    color: #fff;
    border: none;
    padding: 6px 14px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.9rem;
    cursor: pointer;
}
.btn-flag-corrupt:hover { background: #e08600; }
.btn-flag-corrupt:disabled { opacity: 0.5; cursor: not-allowed; }
.footer {
    text-align: center;
    padding: 24px;
    color: var(--text-secondary);
    font-size: 12px;
}
"""
    if not interactive:
        return base

    return base + """
/* Interactive mode styles */
.sticky-bar {
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--group-bg);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 13px;
    box-shadow: var(--shadow);
    border-radius: 10px;
    margin-bottom: 16px;
}
#review-count {
    font-weight: 600;
}
.sticky-stats {
    color: var(--text-secondary);
}
.sticky-hint {
    margin-left: auto;
    color: var(--text-secondary);
    font-size: 12px;
}
.group-actions {
    display: flex;
    gap: 8px;
    margin-left: auto;
}
.group-count { margin-left: 0; }
.btn {
    font-size: 12px;
    font-weight: 600;
    padding: 5px 14px;
    border-radius: 8px;
    border: none;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.1s;
}
.btn:active { transform: scale(0.96); }
.btn-merge {
    background: var(--keeper-border);
    color: #fff;
}
.btn-merge:hover { opacity: 0.85; }
.btn-undo {
    background: var(--border);
    color: var(--text);
}
.btn-undo:hover { opacity: 0.85; }
.item-card.interactive {
    cursor: pointer;
}
.item-card.interactive:hover {
    transform: scale(1.02);
}
.item-card.selected {
    border-color: var(--keeper-border);
    background: var(--keeper-bg);
}
.item-card.interactive:not(.selected) {
    border-color: var(--border);
    background: var(--card-bg);
}
.group.merging {
    opacity: 0.6;
    pointer-events: none;
}
.group.merged {
    transition: max-height 0.4s ease-out, opacity 0.3s, margin 0.4s, padding 0.4s;
    max-height: 0 !important;
    opacity: 0;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden;
    border: none;
    box-shadow: none;
}
.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
.btn-merge.loading {
    min-width: 80px;
}
.btn-merge-all {
    background: #e67e22;
    color: #fff;
    border: none;
    padding: 6px 18px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.9rem;
    cursor: pointer;
}
.btn-merge-all:hover {
    background: #d35400;
}
.btn-merge-all:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
.btn-merge-all-global {
    background: #c0392b;
    color: #fff;
    border: none;
    padding: 6px 14px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.9rem;
    cursor: pointer;
}
.btn-merge-all-global:hover {
    background: #a93226;
}
.btn-merge-all-global:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
/* Size selector */
.size-selector {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
}
.size-selector label {
    color: var(--text-secondary);
    font-weight: 500;
}
.size-selector select {
    font-size: 12px;
    padding: 3px 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--card-bg);
    color: var(--text);
    cursor: pointer;
}
/* Card sizes */
body.size-small .item-card { flex: 0 1 220px; }
body.size-medium .item-card { flex: 0 1 340px; }
body.size-large .item-card { flex: 0 1 480px; }
"""


def _sidebar_css() -> str:
    """CSS for the category filter sidebar and layout wrapper."""
    return """
/* Layout: sidebar + main content */
.layout-wrapper {
    display: flex;
    gap: 24px;
    max-width: 1600px;
    margin: 0 auto;
}
.main-content {
    flex: 1;
    min-width: 0;
}
.sidebar {
    position: sticky;
    top: 60px;
    align-self: flex-start;
    width: 200px;
    flex-shrink: 0;
    background: var(--group-bg);
    border-radius: 12px;
    box-shadow: var(--shadow);
    padding: 12px 0;
    overflow: hidden;
}
.sidebar-title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    padding: 4px 16px 8px;
}
.sidebar-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 8px 16px;
    border: none;
    background: none;
    color: var(--text);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s;
    text-align: left;
}
.sidebar-item:hover {
    background: var(--border);
}
.sidebar-item.active {
    background: var(--exact-bg);
    color: #fff;
}
.sidebar-item.active .sidebar-count {
    color: rgba(255, 255, 255, 0.8);
}
.sidebar-count {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    background: var(--bg);
    padding: 1px 8px;
    border-radius: 10px;
    min-width: 24px;
    text-align: center;
}
.sidebar-item.active .sidebar-count {
    background: rgba(255, 255, 255, 0.2);
}
.sidebar-divider {
    height: 1px;
    background: var(--border);
    margin: 8px 16px;
}
/* Hide groups that don't match the active filter */
.group.category-hidden {
    display: none !important;
}
@media (max-width: 900px) {
    .layout-wrapper { flex-direction: column; }
    .sidebar {
        position: static;
        width: 100%;
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        padding: 8px;
    }
    .sidebar-title {
        width: 100%;
        padding: 4px 8px;
    }
    .sidebar-item {
        flex: 0 0 auto;
        padding: 6px 12px;
        border-radius: 8px;
    }
}
"""


def _interactive_js(keeper_map_json: str) -> str:
    """Return the JavaScript for interactive merge mode."""
    return f"""
<script>
const keeperMap = {keeper_map_json};
// selectedKeepers: gid -> Set of selected UUIDs (multi-select)
const selectedKeepers = {{}};
let totalGroups = document.querySelectorAll('.group[data-group-id]').length;
let mergedCount = 0;
let mergeAllRunning = false;

// Initialize: body size class
document.body.classList.add('size-large');

function playVideo(overlay) {{
    const uuid = overlay.dataset.uuid;
    const wrap = overlay.closest('.thumb-wrap');
    const video = document.createElement('video');
    video.src = '/video/' + uuid;
    video.controls = true;
    video.autoplay = true;
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'contain';
    video.onclick = (e) => e.stopPropagation();
    wrap.innerHTML = '';
    wrap.appendChild(video);
}}

async function flagAllCorrupt() {{
    const btn = document.getElementById('flag-corrupt-btn');
    if (btn) btn.disabled = true;
    try {{
        const resp = await fetch('/api/flag-corrupt', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{all: true}}),
        }});
        const data = await resp.json();
        if (data.ok) {{
            alert('Added ' + data.count + ' suspect corrupt videos to "Media Scanner - Suspect Corrupt" album.');
        }} else {{
            alert(data.error || 'Failed to flag corrupt videos');
        }}
    }} catch (err) {{
        alert('Network error: ' + err.message);
    }}
    if (btn) btn.disabled = false;
}}

// Initialize: select recommended keepers
for (const [gid, uuid] of Object.entries(keeperMap)) {{
    selectedKeepers[gid] = new Set([uuid]);
}}

// Handle clicking an item card to toggle its selection
document.addEventListener('click', (e) => {{
    const card = e.target.closest('.item-card[data-uuid]');
    if (!card) return;
    const group = card.closest('.group[data-group-id]');
    if (!group || group.classList.contains('merging')) return;

    const gid = group.dataset.groupId;
    const uuid = card.dataset.uuid;

    if (!selectedKeepers[gid]) {{
        selectedKeepers[gid] = new Set();
    }}

    // Toggle selection
    if (card.classList.contains('selected')) {{
        card.classList.remove('selected');
        selectedKeepers[gid].delete(uuid);
    }} else {{
        card.classList.add('selected');
        selectedKeepers[gid].add(uuid);
    }}
}});

// Pre-select the recommended keeper in each group on load
document.querySelectorAll('.group[data-group-id]').forEach(group => {{
    const gid = group.dataset.groupId;
    const keepSet = selectedKeepers[gid];
    if (keepSet) {{
        group.querySelectorAll('.item-card').forEach(c => {{
            if (keepSet.has(c.dataset.uuid)) {{
                c.classList.add('selected');
            }} else {{
                c.classList.remove('selected');
            }}
        }});
    }}
}});

updateCounts();

function changeSize(size) {{
    document.body.classList.remove('size-small', 'size-medium', 'size-large');
    document.body.classList.add('size-' + size);
}}

async function mergeGroup(groupId) {{
    const group = document.querySelector(`.group[data-group-id="${{groupId}}"]`);
    if (!group) return false;

    const keepSet = selectedKeepers[groupId] || new Set();

    const btn = group.querySelector('.btn-merge');
    if (btn) {{
        btn.textContent = 'Merging...';
        btn.classList.add('loading');
        btn.disabled = true;
    }}
    group.classList.add('merging');

    try {{
        const resp = await fetch('/api/merge', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                group_id: Number(groupId),
                keep_uuids: Array.from(keepSet),
            }}),
        }});
        const data = await resp.json();
        if (data.ok) {{
            mergedCount++;
            group.style.maxHeight = group.scrollHeight + 'px';
            group.offsetHeight;
            group.classList.remove('merging');
            group.classList.add('merged');
            updateCounts();
            return true;
        }} else {{
            group.classList.remove('merging');
            if (btn) {{
                btn.textContent = 'Merge';
                btn.classList.remove('loading');
                btn.disabled = false;
            }}
            if (!mergeAllRunning) alert(data.error || 'Merge failed');
            return false;
        }}
    }} catch (err) {{
        group.classList.remove('merging');
        if (btn) {{
            btn.textContent = 'Merge';
            btn.classList.remove('loading');
            btn.disabled = false;
        }}
        if (!mergeAllRunning) alert('Network error: ' + err.message);
        return false;
    }}
}}

async function mergeAll() {{
    const btn = document.getElementById('merge-all-btn');
    const groups = getVisibleGroups();
    if (groups.length === 0) return;

    btn.disabled = true;
    mergeAllRunning = true;
    let done = 0;
    let failed = 0;
    const total = groups.length;
    btn.textContent = `Merging 0/${{total}}...`;

    for (const group of groups) {{
        const gid = group.dataset.groupId;
        const ok = await mergeGroup(gid);
        done++;
        if (!ok) failed++;
        btn.textContent = `Merging ${{done}}/${{total}}...`;
    }}

    mergeAllRunning = false;
    if (failed > 0) {{
        btn.textContent = `Merge All (${{failed}} failed)`;
        btn.disabled = false;
    }} else {{
        btn.textContent = 'All Merged';
    }}
}}

// Category filter
let activeFilter = 'all';
const categoryLabels = {json.dumps(CATEGORY_LABELS)};

function filterCategory(category) {{
    activeFilter = category;
    // Update sidebar active state
    document.querySelectorAll('.sidebar-item').forEach(btn => {{
        btn.classList.toggle('active', btn.dataset.filter === category);
    }});
    // Show/hide groups
    document.querySelectorAll('.group[data-group-id]').forEach(group => {{
        const tags = (group.dataset.tags || '').split(' ');
        if (category === 'all' || tags.includes(category)) {{
            group.classList.remove('category-hidden');
        }} else {{
            group.classList.add('category-hidden');
        }}
    }});
    updateMergeButtonLabels();
    updateCounts();
}}

function updateMergeButtonLabels() {{
    const label = categoryLabels[activeFilter] || 'All';
    const btn = document.getElementById('merge-all-btn');
    if (btn && !btn.disabled) {{
        btn.textContent = activeFilter === 'all' ? 'Merge All' : `Merge All ${{label}}`;
    }}
}}

function groupMatchesFilter(group) {{
    if (activeFilter === 'all') return true;
    const tags = (group.dataset.tags || '').split(' ');
    return tags.includes(activeFilter);
}}

function getVisibleGroups() {{
    const all = document.querySelectorAll('.group[data-group-id]:not(.merged)');
    return Array.from(all).filter(g => !g.classList.contains('category-hidden') && groupMatchesFilter(g));
}}

function updateCounts() {{
    const remaining = document.querySelectorAll('.group[data-group-id]:not(.merged)').length;
    const visible = getVisibleGroups().length;
    const filterLabel = activeFilter === 'all' ? '' : ` (${{activeFilter.replace('_', ' ')}})`;
    document.getElementById('review-count').textContent =
        `${{mergedCount}} merged, ${{visible}} visible${{filterLabel}}, ${{remaining}} total remaining`;
    document.getElementById('sticky-stats').textContent =
        mergedCount > 0
            ? `${{mergedCount}} groups added to album`
            : '';
    const maBtn = document.getElementById('merge-all-btn');
    if (maBtn && !mergeAllRunning && visible === 0) {{
        maBtn.textContent = 'All Merged';
        maBtn.disabled = true;
    }}
    // Update sidebar counts after merges
    updateSidebarCounts();
}}

function updateSidebarCounts() {{
    const allGroups = document.querySelectorAll('.group[data-group-id]:not(.merged)');
    const counts = {{}};
    let total = 0;
    allGroups.forEach(g => {{
        const tags = (g.dataset.tags || '').split(' ');
        tags.forEach(tag => {{
            counts[tag] = (counts[tag] || 0) + 1;
        }});
        total++;
    }});
    document.querySelectorAll('.sidebar-item').forEach(btn => {{
        const f = btn.dataset.filter;
        const countEl = btn.querySelector('.sidebar-count');
        if (f === 'all') {{
            countEl.textContent = total;
        }} else {{
            countEl.textContent = counts[f] || 0;
        }}
    }});
}}
</script>"""


def _build_sidebar_html(
    groups: list[DuplicateGroup], active_filter: str = "all"
) -> str:
    """Build the category filter sidebar HTML."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for g in groups:
        for tag in group_tags(g):
            counts[tag] += 1

    # Split into type categories and size categories
    type_keys = ["all", "exact_photo", "near_photo", "exact_video", "near_video", "live_photo"]
    size_keys = ["small_files", "large_files"]

    def _buttons(keys: list[str]) -> str:
        items = []
        for key in keys:
            count = len(groups) if key == "all" else counts.get(key, 0)
            label = CATEGORY_LABELS[key]
            if key == "all" or count > 0:
                active = " active" if key == active_filter else ""
                items.append(
                    f'<button class="sidebar-item{active}" data-filter="{key}"'
                    f' onclick="filterCategory(\'{key}\')">'
                    f'<span class="sidebar-label">{label}</span>'
                    f'<span class="sidebar-count">{count}</span>'
                    f'</button>'
                )
        return "".join(items)

    size_section = ""
    if any(counts.get(k, 0) > 0 for k in size_keys):
        size_section = f"""
        <div class="sidebar-divider"></div>
        <div class="sidebar-title">Size</div>
        {_buttons(size_keys)}"""

    quality_keys = ["suspect_corrupt"]
    quality_section = ""
    if any(counts.get(k, 0) > 0 for k in quality_keys):
        quality_section = f"""
        <div class="sidebar-divider"></div>
        <div class="sidebar-title">Quality</div>
        {_buttons(quality_keys)}"""

    return f"""
    <nav class="sidebar" id="sidebar">
        <div class="sidebar-title">Categories</div>
        {_buttons(type_keys)}
        {size_section}
        {quality_section}
    </nav>"""


def generate_page_html(
    groups: list[DuplicateGroup],
    config: Config,
    page: int,
    total_pages: int,
    total_groups: int,
    actions: dict[str, ActionRecord] | None = None,
    title: str = "Duplicate Review",
    per_page: int = PAGE_SIZE,
    active_filter: str = "all",
) -> str:
    """Generate interactive HTML for a single page of groups (server-side pagination)."""
    actions = actions or {}

    groups_html = []
    for idx, group in enumerate(groups, 1):
        groups_html.append(
            _build_group_html(idx, group, config, actions, interactive=True)
        )

    from media_scanner.ui.formatters import format_count

    stats_summary = (
        f"{format_count(total_groups)} groups total &middot; "
        f"Page {page} of {total_pages}"
    )

    keeper_map = {
        g.group_id: g.recommended_keep_uuid
        for g in groups
        if g.recommended_keep_uuid
    }
    keeper_map_json = json.dumps(keeper_map)

    pagination = _build_pagination_html(page, total_pages, per_page, active_filter)

    filter_label = CATEGORY_LABELS.get(active_filter, "All")
    merge_page_label = "Merge All on Page" if active_filter == "all" else f"Merge {filter_label} on Page"
    merge_all_label = "Merge All Groups" if active_filter == "all" else f"Merge All {filter_label}"

    per_page_options = [25, 50, 100, 200, 500]
    per_page_select = "".join(
        f'<option value="{n}"{" selected" if n == per_page else ""}>{n}</option>'
        for n in per_page_options
    )

    sticky_header = f"""
    <div class="sticky-bar" id="sticky-bar">
        <span id="review-count">{total_groups} groups remaining</span>
        <span class="sticky-stats" id="sticky-stats"></span>
        <button class="btn btn-merge-all" id="merge-all-btn" onclick="mergeAllOnPage()">{merge_page_label}</button>
        <button class="btn btn-merge-all-global" id="merge-all-global-btn" onclick="mergeAllGroups()">{merge_all_label}</button>
        {'<button class="btn btn-flag-corrupt" id="flag-corrupt-btn" onclick="flagAllCorrupt()">Add Corrupt to Album</button>' if active_filter == "suspect_corrupt" else ''}
        <div class="size-selector">
            <label for="per-page-select">Per page:</label>
            <select id="per-page-select" onchange="changePerPage(this.value)">
                {per_page_select}
            </select>
        </div>
        <div class="size-selector">
            <label for="size-select">Size:</label>
            <select id="size-select" onchange="changeSize(this.value)">
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large" selected>Large</option>
            </select>
        </div>
        <span class="sticky-hint">Click photos to keep (green border). Unselected photos go to delete album.</span>
    </div>"""

    js_block = _paginated_interactive_js(keeper_map_json, page, total_pages, per_page, active_filter)

    # Pass all groups from server for accurate sidebar counts
    sidebar = _build_sidebar_html(ReviewHandler_all_groups or groups, active_filter)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Page {page}</title>
<style>
{_css(True)}
{_pagination_css()}
{_sidebar_css()}
</style>
</head>
<body>
<div class="header">
    <h1>{title}</h1>
</div>
{sticky_header}
<div class="layout-wrapper">
{sidebar}
<div class="main-content">
<div class="stats">{stats_summary}</div>
{pagination}
{''.join(groups_html)}
{pagination}
<div class="footer">Generated by media-scanner</div>
</div>
</div>
{js_block}
</body>
</html>"""


# Module-level reference to all groups (set by server.py before generating pages)
ReviewHandler_all_groups: list[DuplicateGroup] | None = None


def _build_pagination_html(
    page: int, total_pages: int, per_page: int = PAGE_SIZE, active_filter: str = "all"
) -> str:
    """Build pagination controls with prev/next and page numbers."""
    if total_pages <= 1:
        return ""

    pp = f"&per_page={per_page}" if per_page != PAGE_SIZE else ""
    ff = f"&filter={active_filter}" if active_filter != "all" else ""
    pp = pp + ff
    links = []

    if page > 1:
        links.append(f'<a class="page-link" href="/?page={page - 1}{pp}">&laquo; Prev</a>')
    else:
        links.append('<span class="page-link disabled">&laquo; Prev</span>')

    pages_to_show: set[int] = set()
    pages_to_show.add(1)
    pages_to_show.add(total_pages)
    for p in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        pages_to_show.add(p)

    last = 0
    for p in sorted(pages_to_show):
        if p - last > 1:
            links.append('<span class="page-ellipsis">&hellip;</span>')
        if p == page:
            links.append(f'<span class="page-link current">{p}</span>')
        else:
            links.append(f'<a class="page-link" href="/?page={p}{pp}">{p}</a>')
        last = p

    if page < total_pages:
        links.append(f'<a class="page-link" href="/?page={page + 1}{pp}">Next &raquo;</a>')
    else:
        links.append('<span class="page-link disabled">Next &raquo;</span>')

    return f'<div class="pagination">{"".join(links)}</div>'


def _pagination_css() -> str:
    """CSS for pagination controls."""
    return """
.pagination {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 4px;
    padding: 16px 0;
}
.page-link {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
    color: var(--text);
    background: var(--card-bg);
    border: 1px solid var(--border);
    transition: background 0.15s;
}
.page-link:hover:not(.disabled):not(.current) {
    background: var(--border);
}
.page-link.current {
    background: var(--exact-bg);
    color: #fff;
    border-color: var(--exact-bg);
}
.page-link.disabled {
    opacity: 0.4;
    cursor: default;
}
.page-ellipsis {
    padding: 6px 4px;
    color: var(--text-secondary);
}"""


def _paginated_interactive_js(
    keeper_map_json: str, page: int, total_pages: int,
    per_page: int = PAGE_SIZE, active_filter: str = "all",
) -> str:
    """JS for paginated interactive mode with Merge All on Page."""
    return f"""
<script>
const keeperMap = {keeper_map_json};
const selectedKeepers = {{}};
const currentPage = {page};
const totalPages = {total_pages};
const perPage = {per_page};
const activeFilter = '{active_filter}';
let mergeAllRunning = false;

document.body.classList.add('size-large');

function buildUrl(params) {{
    const p = new URLSearchParams();
    p.set('page', params.page || 1);
    if (params.per_page && params.per_page !== {PAGE_SIZE}) p.set('per_page', params.per_page);
    if (params.filter && params.filter !== 'all') p.set('filter', params.filter);
    return '/?' + p.toString();
}}

function changePerPage(value) {{
    window.location.href = buildUrl({{page: 1, per_page: value, filter: activeFilter}});
}}

function playVideo(overlay) {{
    const uuid = overlay.dataset.uuid;
    const wrap = overlay.closest('.thumb-wrap');
    const video = document.createElement('video');
    video.src = '/video/' + uuid;
    video.controls = true;
    video.autoplay = true;
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'contain';
    video.onclick = (e) => e.stopPropagation();
    wrap.innerHTML = '';
    wrap.appendChild(video);
}}

async function flagAllCorrupt() {{
    const btn = document.getElementById('flag-corrupt-btn');
    if (btn) btn.disabled = true;
    try {{
        const resp = await fetch('/api/flag-corrupt', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{all: true}}),
        }});
        const data = await resp.json();
        if (data.ok) {{
            alert('Added ' + data.count + ' suspect corrupt videos to "Media Scanner - Suspect Corrupt" album.');
        }} else {{
            alert(data.error || 'Failed to flag corrupt videos');
        }}
    }} catch (err) {{
        alert('Network error: ' + err.message);
    }}
    if (btn) btn.disabled = false;
}}

for (const [gid, uuid] of Object.entries(keeperMap)) {{
    selectedKeepers[gid] = new Set([uuid]);
}}

document.addEventListener('click', (e) => {{
    const card = e.target.closest('.item-card[data-uuid]');
    if (!card) return;
    const group = card.closest('.group[data-group-id]');
    if (!group || group.classList.contains('merging')) return;

    const gid = group.dataset.groupId;
    const uuid = card.dataset.uuid;

    if (!selectedKeepers[gid]) {{
        selectedKeepers[gid] = new Set();
    }}

    if (card.classList.contains('selected')) {{
        card.classList.remove('selected');
        selectedKeepers[gid].delete(uuid);
    }} else {{
        card.classList.add('selected');
        selectedKeepers[gid].add(uuid);
    }}
}});

document.querySelectorAll('.group[data-group-id]').forEach(group => {{
    const gid = group.dataset.groupId;
    const keepSet = selectedKeepers[gid];
    if (keepSet) {{
        group.querySelectorAll('.item-card').forEach(c => {{
            if (keepSet.has(c.dataset.uuid)) {{
                c.classList.add('selected');
            }} else {{
                c.classList.remove('selected');
            }}
        }});
    }}
}});

function changeSize(size) {{
    document.body.classList.remove('size-small', 'size-medium', 'size-large');
    document.body.classList.add('size-' + size);
}}

async function mergeGroup(groupId) {{
    const group = document.querySelector(`.group[data-group-id="${{groupId}}"]`);
    if (!group) return false;

    const keepSet = selectedKeepers[groupId] || new Set();

    const btn = group.querySelector('.btn-merge');
    if (btn) {{
        btn.textContent = 'Merging...';
        btn.classList.add('loading');
        btn.disabled = true;
    }}
    group.classList.add('merging');

    try {{
        const resp = await fetch('/api/merge', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                group_id: Number(groupId),
                keep_uuids: Array.from(keepSet),
            }}),
        }});
        const data = await resp.json();
        if (data.ok) {{
            group.style.maxHeight = group.scrollHeight + 'px';
            group.offsetHeight;
            group.classList.remove('merging');
            group.classList.add('merged');
            updateCounts();
            return true;
        }} else {{
            group.classList.remove('merging');
            if (btn) {{
                btn.textContent = 'Merge';
                btn.classList.remove('loading');
                btn.disabled = false;
            }}
            if (!mergeAllRunning) alert(data.error || 'Merge failed');
            return false;
        }}
    }} catch (err) {{
        group.classList.remove('merging');
        if (btn) {{
            btn.textContent = 'Merge';
            btn.classList.remove('loading');
            btn.disabled = false;
        }}
        if (!mergeAllRunning) alert('Network error: ' + err.message);
        return false;
    }}
}}

// Category filter — server-side, so clicking navigates
const categoryLabels = {json.dumps(CATEGORY_LABELS)};

function filterCategory(category) {{
    window.location.href = buildUrl({{page: 1, per_page: perPage, filter: category}});
}}

function getVisibleGroups() {{
    return document.querySelectorAll('.group[data-group-id]:not(.merged)');
}}

async function updateSidebarCounts() {{
    try {{
        const resp = await fetch('/api/summary');
        const data = await resp.json();
        const counts = data.category_counts || {{}};
        const total = data.total_groups || 0;
        document.querySelectorAll('.sidebar-item').forEach(btn => {{
            const f = btn.dataset.filter;
            const countEl = btn.querySelector('.sidebar-count');
            if (f === 'all') {{
                countEl.textContent = total;
            }} else {{
                countEl.textContent = counts[f] || 0;
            }}
        }});
    }} catch (err) {{
        // Fallback: count DOM elements on current page
        const allGroups = document.querySelectorAll('.group[data-group-id]:not(.merged)');
        const counts = {{}};
        let total = 0;
        allGroups.forEach(g => {{
            const tags = (g.dataset.tags || '').split(' ');
            tags.forEach(tag => {{
                counts[tag] = (counts[tag] || 0) + 1;
            }});
            total++;
        }});
        document.querySelectorAll('.sidebar-item').forEach(btn => {{
            const f = btn.dataset.filter;
            const countEl = btn.querySelector('.sidebar-count');
            if (f === 'all') {{
                countEl.textContent = total;
            }} else {{
                countEl.textContent = counts[f] || 0;
            }}
        }});
    }}
}}

async function mergeAllOnPage() {{
    const btn = document.getElementById('merge-all-btn');
    const groups = getVisibleGroups();
    if (groups.length === 0) return;

    btn.disabled = true;
    mergeAllRunning = true;
    let done = 0;
    let failed = 0;
    const total = groups.length;
    btn.textContent = `Merging 0/${{total}}...`;

    for (const group of groups) {{
        const gid = group.dataset.groupId;
        const ok = await mergeGroup(gid);
        done++;
        if (!ok) failed++;
        btn.textContent = `Merging ${{done}}/${{total}}...`;
    }}

    mergeAllRunning = false;

    if (failed > 0) {{
        const failLabel = activeFilter === 'all' ? 'Merge All on Page' : `Merge ${{categoryLabels[activeFilter]}} on Page`;
        btn.textContent = `${{failLabel}} (${{failed}} failed)`;
        btn.disabled = false;
    }} else {{
        btn.textContent = 'Checking...';
        try {{
            const resp = await fetch('/api/summary');
            const data = await resp.json();
            if (data.total_groups > 0) {{
                // Check if remaining groups match the active filter
                const filterCount = activeFilter === 'all'
                    ? data.total_groups
                    : (data.category_counts || {{}})[activeFilter] || 0;
                if (filterCount > 0) {{
                    btn.textContent = `${{filterCount}} more — Reloading...`;
                    setTimeout(() => window.location.href = buildUrl({{page: 1, per_page: perPage, filter: activeFilter}}), 800);
                }} else {{
                    // This category is done but others remain — navigate to all
                    btn.textContent = `${{data.total_groups}} remaining — Reloading...`;
                    setTimeout(() => window.location.href = buildUrl({{page: 1, per_page: perPage, filter: 'all'}}), 800);
                }}
            }} else {{
                btn.textContent = 'All Done!';
                document.getElementById('merge-all-global-btn').disabled = true;
                document.getElementById('review-count').textContent = 'All groups merged!';
            }}
        }} catch (err) {{
            btn.textContent = 'Page Done — Reload to continue';
            btn.disabled = false;
        }}
    }}
}}

async function mergeAllGroups() {{
    const globalBtn = document.getElementById('merge-all-global-btn');

    // Fetch all groups from the server
    let allGroups;
    try {{
        const resp = await fetch('/api/all-groups');
        const data = await resp.json();
        allGroups = data.groups;
    }} catch (err) {{
        alert('Failed to fetch groups: ' + err.message);
        return;
    }}

    if (!allGroups || allGroups.length === 0) {{
        alert('No groups to merge.');
        return;
    }}

    // Filter by active category if not "all"
    if (activeFilter !== 'all') {{
        allGroups = allGroups.filter(g => (g.tags || []).includes(activeFilter));
        if (allGroups.length === 0) {{
            alert('No groups matching the current filter.');
            return;
        }}
    }}

    const totalItems = allGroups.reduce((s, g) => s + g.item_count, 0);
    const totalKeep = allGroups.length;
    const totalDelete = totalItems - totalKeep;
    const filterLabel = activeFilter === 'all' ? '' : ` (${{activeFilter.replace(/_/g, ' ')}})`;

    if (!confirm(
        `Merge${{filterLabel}} ${{allGroups.length}} groups across all pages?\\n\\n` +
        `This will:\\n` +
        `  • Keep ${{totalKeep}} recommended items\\n` +
        `  • Add ${{totalDelete}} duplicates to the "To Delete" album\\n\\n` +
        `This cannot be undone. Continue?`
    )) {{
        return;
    }}

    globalBtn.disabled = true;
    mergeAllRunning = true;
    let done = 0;
    let failed = 0;
    const total = allGroups.length;
    globalBtn.textContent = `Merging 0/${{total}}...`;

    for (const g of allGroups) {{
        const keepUuids = selectedKeepers[g.group_id]
            ? Array.from(selectedKeepers[g.group_id])
            : g.keep_uuids;

        try {{
            const resp = await fetch('/api/merge', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    group_id: g.group_id,
                    keep_uuids: keepUuids,
                }}),
            }});
            const data = await resp.json();
            if (data.ok) {{
                const el = document.querySelector(`.group[data-group-id="${{g.group_id}}"]`);
                if (el) {{
                    el.classList.add('merged');
                }}
            }} else {{
                failed++;
            }}
        }} catch (err) {{
            failed++;
        }}
        done++;
        globalBtn.textContent = `Merging ${{done}}/${{total}}...`;
    }}

    mergeAllRunning = false;
    await updateSidebarCounts();

    if (failed > 0) {{
        globalBtn.textContent = `Done (${{failed}} failed)`;
        globalBtn.disabled = false;
    }} else {{
        // Check if there are still groups remaining
        try {{
            const resp = await fetch('/api/summary');
            const data = await resp.json();
            if (data.total_groups > 0) {{
                // Navigate to show remaining groups
                globalBtn.textContent = `${{data.total_groups}} remaining — Reloading...`;
                setTimeout(() => window.location.href = buildUrl({{page: 1, per_page: perPage, filter: 'all'}}), 800);
            }} else {{
                globalBtn.textContent = 'All Done!';
                document.getElementById('merge-all-btn').disabled = true;
                document.getElementById('review-count').textContent = 'All groups merged!';
            }}
        }} catch (err) {{
            globalBtn.textContent = 'Done';
            globalBtn.disabled = false;
        }}
    }}
}}

async function updateCounts() {{
    const visible = getVisibleGroups().length;
    const filterLabel = activeFilter === 'all' ? '' : ` (${{activeFilter.replace(/_/g, ' ')}})`;
    try {{
        const resp = await fetch('/api/summary');
        const data = await resp.json();
        document.getElementById('review-count').textContent =
            activeFilter === 'all'
                ? `${{data.total_groups}} groups remaining`
                : `${{visible}} visible${{filterLabel}}, ${{data.total_groups}} total remaining`;
    }} catch (err) {{
        document.getElementById('review-count').textContent =
            `${{visible}} on this page${{filterLabel}}`;
    }}
    updateSidebarCounts();
}}

updateCounts();
</script>"""
