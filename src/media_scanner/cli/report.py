"""review command - interactive browser-based duplicate review."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.quality_scorer import rank_group
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MatchType
from media_scanner.ui.console import console
from media_scanner.ui.progress import create_progress
from media_scanner.ui.report import generate_report


def _load_groups(cache: CacheDB, config, match_type: str, limit: int):
    """Load, rank, and optionally limit duplicate groups."""
    mt_filter = None
    if match_type == "exact":
        mt_filter = MatchType.EXACT
    elif match_type == "near":
        mt_filter = MatchType.NEAR

    groups = cache.get_duplicate_groups(mt_filter)

    if not groups:
        console.print("[yellow]No duplicate groups found. Run 'media-scanner dupes' first.[/yellow]")
        cache.close()
        raise typer.Exit(1)

    # Filter out groups whose items were removed by a re-scan (stale references)
    stale_ids = [g.group_id for g in groups if not g.items]
    if stale_ids:
        for gid in stale_ids:
            cache.delete_duplicate_group(gid)
        groups = [g for g in groups if g.items]
        if not groups:
            console.print(
                "[yellow]All duplicate groups are stale. Run 'media-scanner dupes' to find fresh duplicates.[/yellow]"
            )
            cache.close()
            raise typer.Exit(1)
        console.print(
            f"  [dim]Skipped {len(stale_ids)} stale group(s) with missing items.[/dim]"
        )

    for group in groups:
        rank_group(group, config, cache=cache)

    total_groups = len(groups)
    if limit > 0 and len(groups) > limit:
        groups = groups[:limit]
        console.print(
            f"  [dim]Showing {limit} of {total_groups} groups. "
            f"Use --limit 0 for all, or --limit N to adjust.[/dim]"
        )

    return groups, mt_filter


def review(
    match_type: Annotated[
        str,
        typer.Option("--type", "-t", help="Filter by match type: exact, near, or all."),
    ] = "all",
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max groups to include (0 = all)."),
    ] = 0,
    port: Annotated[
        int,
        typer.Option("--port", help="Port for the review server."),
    ] = 8777,
    static: Annotated[
        bool,
        typer.Option("--static", help="Generate a static HTML report instead of the interactive server."),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output HTML file path (only with --static)."),
    ] = Path("duplicates-report.html"),
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Don't open in a browser (only with --static)."),
    ] = False,
) -> None:
    """Review duplicate groups in an interactive browser UI."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    groups, mt_filter = _load_groups(cache, config, match_type, limit)

    # Load any existing actions
    pending = cache.get_pending_actions()
    actions = {a.uuid: a for a in pending}

    total_items = sum(len(g.items) for g in groups)

    title = "Duplicate Review"
    if mt_filter == MatchType.EXACT:
        title = "Exact Duplicates Review"
    elif mt_filter == MatchType.NEAR:
        title = "Near Duplicates Review"

    if static:
        _generate_static(groups, config, actions, title, total_items, output, no_open)
        cache.close()
    else:
        _run_server(groups, config, cache, actions, title, total_items, port)


def _generate_static(groups, config, actions, title, total_items, output, no_open):
    """Generate a static HTML report file."""
    console.print(
        f"[bold]Generating report for {len(groups)} groups ({total_items} items)...[/bold]"
    )

    with create_progress() as progress:
        task = progress.add_task("Thumbnails", total=total_items)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        html = generate_report(
            groups, config, actions=actions, title=title,
            progress_callback=on_progress,
        )

    output.write_text(html, encoding="utf-8")
    console.print(f"  Report saved to [cyan]{output.resolve()}[/cyan]")

    if not no_open:
        webbrowser.open(f"file://{output.resolve()}")


def _run_server(groups, config, cache, actions, title, total_items, port):
    """Start the interactive review server."""
    from media_scanner.ui.server import start_server

    console.print(
        f"[bold]Starting review server for {len(groups)} groups ({total_items} items)...[/bold]"
    )

    server = start_server(groups, cache, config, port=port, title=title)
    url = f"http://127.0.0.1:{port}"

    console.print(f"  Review server running at [cyan]{url}[/cyan]")
    console.print("  [dim]Press Ctrl+C to stop.[/dim]")
    console.print(
        "  [dim]Click Merge to add duplicates to the "
        "\"Media Scanner - To Delete\" album in Photos.app.[/dim]"
    )

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[bold]Server stopped.[/bold]")
    finally:
        server.server_close()
        cache.close()
