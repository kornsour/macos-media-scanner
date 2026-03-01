"""Generate self-contained HTML report for duplicate groups."""

from __future__ import annotations

import base64
import html as html_mod
import io
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PIL import Image

from media_scanner.core.quality_scorer import score_item
from media_scanner.data.models import ActionType, MatchType

if TYPE_CHECKING:
    from media_scanner.config import Config
    from media_scanner.data.models import ActionRecord, DuplicateGroup, MediaItem

logger = logging.getLogger(__name__)

THUMB_SIZE = 240
THUMB_QUALITY = 65


def _thumbnail_b64(item: MediaItem) -> str | None:
    """Generate a base64-encoded JPEG thumbnail for an item."""
    if not item.path or not item.path.exists():
        return None
    try:
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

    # Thumbnail — server-served lazy URL in interactive mode, inline b64 in static
    if interactive:
        img_tag = (
            f'<img src="/thumb/{item.uuid}" alt="{html_mod.escape(item.filename)}" loading="lazy">'
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

    from media_scanner.ui.formatters import format_date, format_resolution, format_size

    date_str = format_date(item.date_created)
    size_str = format_size(item.file_size)
    res_str = format_resolution(item.width, item.height)

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
            <div class="item-meta">{size_str} &middot; {res_str}</div>
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

    data_attr = f' data-group-id="{group.group_id}"' if interactive else ""

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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{_css(interactive)}
</style>
</head>
<body>
<div class="header">
    <h1>{title}</h1>
</div>
{sticky_header}
<div class="stats">{stats_summary}</div>
{''.join(groups_html)}
<div class="footer">Generated by media-scanner</div>
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
    max-width: 1400px;
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
    const groups = document.querySelectorAll('.group[data-group-id]:not(.merged)');
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

function updateCounts() {{
    const remaining = document.querySelectorAll('.group[data-group-id]:not(.merged)').length;
    document.getElementById('review-count').textContent =
        `${{mergedCount}} merged, ${{remaining}} remaining`;
    document.getElementById('sticky-stats').textContent =
        mergedCount > 0
            ? `${{mergedCount}} groups added to album`
            : '';
    const maBtn = document.getElementById('merge-all-btn');
    if (maBtn && !mergeAllRunning && remaining === 0) {{
        maBtn.textContent = 'All Merged';
        maBtn.disabled = true;
    }}
}}
</script>"""


def generate_page_html(
    groups: list[DuplicateGroup],
    config: Config,
    page: int,
    total_pages: int,
    total_groups: int,
    actions: dict[str, ActionRecord] | None = None,
    title: str = "Duplicate Review",
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

    pagination = _build_pagination_html(page, total_pages)

    sticky_header = f"""
    <div class="sticky-bar" id="sticky-bar">
        <span id="review-count">{total_groups} groups remaining</span>
        <span class="sticky-stats" id="sticky-stats"></span>
        <button class="btn btn-merge-all" id="merge-all-btn" onclick="mergeAllOnPage()">Merge All on Page</button>
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

    js_block = _paginated_interactive_js(keeper_map_json, page, total_pages)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Page {page}</title>
<style>
{_css(True)}
{_pagination_css()}
</style>
</head>
<body>
<div class="header">
    <h1>{title}</h1>
</div>
{sticky_header}
<div class="stats">{stats_summary}</div>
{pagination}
{''.join(groups_html)}
{pagination}
<div class="footer">Generated by media-scanner</div>
{js_block}
</body>
</html>"""


def _build_pagination_html(page: int, total_pages: int) -> str:
    """Build pagination controls with prev/next and page numbers."""
    if total_pages <= 1:
        return ""

    links = []

    if page > 1:
        links.append(f'<a class="page-link" href="/?page={page - 1}">&laquo; Prev</a>')
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
            links.append(f'<a class="page-link" href="/?page={p}">{p}</a>')
        last = p

    if page < total_pages:
        links.append(f'<a class="page-link" href="/?page={page + 1}">Next &raquo;</a>')
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


def _paginated_interactive_js(keeper_map_json: str, page: int, total_pages: int) -> str:
    """JS for paginated interactive mode with Merge All on Page."""
    return f"""
<script>
const keeperMap = {keeper_map_json};
const selectedKeepers = {{}};
const currentPage = {page};
const totalPages = {total_pages};
let mergeAllRunning = false;

document.body.classList.add('size-large');

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

async function mergeAllOnPage() {{
    const btn = document.getElementById('merge-all-btn');
    const groups = document.querySelectorAll('.group[data-group-id]:not(.merged)');
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
        btn.textContent = `Merge All on Page (${{failed}} failed)`;
        btn.disabled = false;
    }} else {{
        btn.textContent = 'Checking...';
        try {{
            const resp = await fetch('/api/summary');
            const data = await resp.json();
            if (data.total_groups > 0) {{
                btn.textContent = `${{data.total_groups}} more — Reloading...`;
                setTimeout(() => window.location.href = '/?page=1', 800);
            }} else {{
                btn.textContent = 'All Done!';
                document.getElementById('review-count').textContent = 'All groups merged!';
            }}
        }} catch (err) {{
            btn.textContent = 'Page Done — Reload to continue';
            btn.disabled = false;
        }}
    }}
}}

async function updateCounts() {{
    const remaining = document.querySelectorAll('.group[data-group-id]:not(.merged)').length;
    try {{
        const resp = await fetch('/api/summary');
        const data = await resp.json();
        document.getElementById('review-count').textContent =
            `${{data.total_groups}} groups remaining`;
    }} catch (err) {{
        document.getElementById('review-count').textContent =
            `${{remaining}} on this page`;
    }}
}}

updateCounts();
</script>"""
