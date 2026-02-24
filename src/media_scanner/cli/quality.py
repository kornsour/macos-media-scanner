"""quality command - surface low-quality photos."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaType
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count, format_date, format_resolution, format_size


def quality(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of items to show."),
    ] = 50,
    screenshots: Annotated[
        bool,
        typer.Option("--screenshots", help="Include screenshots."),
    ] = False,
) -> None:
    """Surface low-quality photos (low resolution, low Apple score)."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    items = cache.get_all_items()

    # Filter to photos only
    photos = [
        i for i in items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
    ]
    if not screenshots:
        photos = [i for i in photos if not i.is_screenshot]

    # Score by resolution (lower = worse) and Apple score
    def quality_key(item):
        pixels = item.width * item.height
        apple = item.apple_score if item.apple_score is not None else 0.5
        # Lower score = lower quality = should appear first
        return pixels * 0.5 + apple * 1_000_000

    photos.sort(key=quality_key)
    low_quality = photos[:limit]

    if not low_quality:
        console.print("[green]No low-quality photos found.[/green]")
        cache.close()
        return

    table = Table(title=f"Lowest Quality Photos (bottom {limit})")
    table.add_column("#", style="bold", width=6)
    table.add_column("Filename", style="cyan")
    table.add_column("Resolution", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Apple Score", justify="right")
    table.add_column("Date", justify="right")

    for idx, item in enumerate(low_quality, 1):
        apple_str = f"{item.apple_score:.2f}" if item.apple_score is not None else "—"
        table.add_row(
            str(idx),
            item.filename,
            format_resolution(item.width, item.height),
            format_size(item.file_size),
            apple_str,
            format_date(item.date_created),
        )

    console.print(table)
    cache.close()
