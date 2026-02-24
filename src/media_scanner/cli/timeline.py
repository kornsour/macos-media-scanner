"""timeline command - find gaps and dense periods in photo history."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from media_scanner.cli.app import get_config
from media_scanner.core.metadata_analyzer import find_timeline_gaps, get_timeline
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count


def timeline(
    granularity: Annotated[
        str,
        typer.Option("--by", help="Granularity: 'month' or 'year'."),
    ] = "month",
    show_gaps: Annotated[
        bool,
        typer.Option("--gaps/--no-gaps", help="Highlight gaps in timeline."),
    ] = True,
) -> None:
    """Show your photo timeline and find gaps."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    periods = get_timeline(cache, granularity=granularity)

    if not periods:
        console.print("[yellow]No dated items found.[/yellow]")
        cache.close()
        return

    # Find max for bar chart scaling
    max_count = max(p.count for p in periods)

    table = Table(title=f"Photo Timeline (by {granularity})")
    table.add_column("Period", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Distribution")

    for period in periods:
        bar_len = int((period.count / max_count) * 40)
        bar = "#" * bar_len
        density = "green" if period.count > max_count * 0.5 else "yellow" if period.count > max_count * 0.1 else "dim"
        table.add_row(
            period.label,
            format_count(period.count),
            f"[{density}]{bar}[/{density}]",
        )

    console.print(table)

    if show_gaps and granularity == "month":
        gaps = find_timeline_gaps(periods)
        if gaps:
            console.print(f"\n[bold yellow]Timeline gaps ({len(gaps)} found):[/bold yellow]")
            for gap_start, gap_end in gaps:
                console.print(f"  Gap: {gap_start} → {gap_end}")
        else:
            console.print("\n[green]No significant gaps found.[/green]")

    console.print(f"\n[dim]Total periods: {len(periods)}, "
                  f"Total items with dates: {format_count(sum(p.count for p in periods))}[/dim]")

    cache.close()
