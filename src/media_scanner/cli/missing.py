"""missing-meta command - find photos missing dates, GPS, faces, etc."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.metadata_analyzer import get_missing_metadata
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count
from media_scanner.ui.tables import media_item_table


def missing_meta(
    show_items: Annotated[
        bool,
        typer.Option("--show/--summary", help="Show individual items or just summary."),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max items to show per category."),
    ] = 20,
) -> None:
    """Find photos missing dates, GPS, faces, or keywords."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    report = get_missing_metadata(cache)

    console.print("[bold]Missing Metadata Report[/bold]\n")
    console.print(f"  No date:     {format_count(len(report.no_date))} items")
    console.print(f"  No GPS:      {format_count(len(report.no_gps))} items")
    console.print(f"  No faces:    {format_count(len(report.no_faces))} items")
    console.print(f"  No keywords: {format_count(len(report.no_keywords))} items")

    if show_items:
        if report.no_date:
            console.print(
                media_item_table(report.no_date[:limit], title=f"Missing Date (top {limit})")
            )
        if report.no_gps:
            console.print(
                media_item_table(report.no_gps[:limit], title=f"Missing GPS (top {limit})")
            )

    cache.close()
