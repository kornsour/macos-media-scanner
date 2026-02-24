"""big-files command - identify largest files / space hogs."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.metadata_analyzer import get_biggest_files
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count, format_size
from media_scanner.ui.tables import media_item_table


def big_files(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of files to show."),
    ] = 50,
) -> None:
    """Show the largest files in your library."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    items = get_biggest_files(cache, limit=limit)

    if not items:
        console.print("[yellow]No items found.[/yellow]")
        cache.close()
        return

    total_size = sum(i.file_size for i in items)
    console.print(
        f"[bold]Top {len(items)} largest files "
        f"(total: {format_size(total_size)}):[/bold]\n"
    )
    console.print(media_item_table(items, title=f"Biggest Files (top {limit})"))

    cache.close()
