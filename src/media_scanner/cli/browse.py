"""browse command - browse all library items in an interactive browser UI."""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console


def browse(
    port: Annotated[
        int,
        typer.Option("--port", help="Port for the browse server."),
    ] = 8778,
    filter_type: Annotated[
        str,
        typer.Option(
            "--filter", "-f",
            help="Pre-filter by category: photo, video, live_photo, screenshot, selfie, burst, favorite, edited, hidden, raw.",
        ),
    ] = "all",
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Don't open the browser automatically."),
    ] = False,
) -> None:
    """Browse all library items and add them to Delete or Keep albums."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    items = cache.get_all_items()
    console.print(f"[bold]Starting browse server for {len(items)} items...[/bold]")

    from media_scanner.ui.server import start_browse_server

    server = start_browse_server(items, cache, config, port=port, title="Library Browser")
    url = f"http://127.0.0.1:{port}"

    if filter_type != "all":
        url += f"?filter={filter_type}"

    console.print(f"  Browse server running at [cyan]{url}[/cyan]")
    console.print("  [dim]Press Ctrl+C to stop.[/dim]")
    console.print(
        "  [dim]Use Delete/Keep buttons to add items to Photos albums.[/dim]"
    )

    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[bold]Server stopped.[/bold]")
    finally:
        server.server_close()
        cache.close()
