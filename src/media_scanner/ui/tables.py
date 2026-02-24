"""Table output helpers using Rich."""

from __future__ import annotations

from rich.table import Table

from media_scanner.data.models import DuplicateGroup, MediaItem
from media_scanner.ui.formatters import format_date, format_resolution, format_size


def duplicate_group_table(
    group: DuplicateGroup,
    group_index: int,
    total_groups: int,
    scores: dict[str, float] | None = None,
) -> Table:
    """Build a Rich table for a duplicate group."""
    title = (
        f"Duplicate Group {group_index}/{total_groups} "
        f"— Type: {group.match_type.value.title()} Match"
    )
    table = Table(title=title, show_lines=True)

    table.add_column("#", style="bold", width=4)
    table.add_column("Filename", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Resolution", justify="right")
    table.add_column("Date", justify="right")
    if scores:
        table.add_column("Score", justify="right", style="yellow")

    for idx, item in enumerate(group.items, 1):
        is_keeper = item.uuid == group.recommended_keep_uuid
        marker = " *" if is_keeper else ""
        row = [
            f"[{idx}]",
            item.filename + marker,
            format_size(item.file_size),
            format_resolution(item.width, item.height),
            format_date(item.date_created),
        ]
        if scores and item.uuid in scores:
            row.append(f"{scores[item.uuid]:.2f}")

        style = "bold green" if is_keeper else None
        table.add_row(*row, style=style)

    return table


def media_item_table(items: list[MediaItem], title: str = "Media Items") -> Table:
    """Build a generic table of media items."""
    table = Table(title=title)
    table.add_column("#", style="bold", width=6)
    table.add_column("Filename", style="cyan")
    table.add_column("Type")
    table.add_column("Size", justify="right")
    table.add_column("Resolution", justify="right")
    table.add_column("Date", justify="right")

    for idx, item in enumerate(items, 1):
        table.add_row(
            str(idx),
            item.filename,
            item.media_type.value,
            format_size(item.file_size),
            format_resolution(item.width, item.height),
            format_date(item.date_created),
        )

    return table


def stats_table(stats: dict) -> Table:
    """Build a library stats table."""
    table = Table(title="Library Statistics", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    from media_scanner.ui.formatters import format_count

    table.add_row("Total items", format_count(stats["total"]))
    table.add_row("Photos", format_count(stats["photos"]))
    table.add_row("Videos", format_count(stats["videos"]))
    table.add_row("Live Photos", format_count(stats["live_photos"]))
    table.add_row("Total size", format_size(stats["total_size"]))
    table.add_row("Favorites", format_count(stats["favorites"]))
    table.add_row("Hidden", format_count(stats["hidden"]))
    table.add_row("Screenshots", format_count(stats["screenshots"]))
    table.add_row("Selfies", format_count(stats["selfies"]))
    table.add_row("Edited", format_count(stats["edited"]))
    table.add_row("With GPS", format_count(stats["with_gps"]))
    table.add_row("Missing date", format_count(stats["no_date"]))
    if stats["oldest"]:
        table.add_row("Oldest item", stats["oldest"][:10])
    if stats["newest"]:
        table.add_row("Newest item", stats["newest"][:10])

    return table
