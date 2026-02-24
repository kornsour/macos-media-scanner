"""stats command - library statistics and health report."""

from __future__ import annotations

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count, format_size
from media_scanner.ui.tables import stats_table

from rich.table import Table


def stats() -> None:
    """Show library statistics and health report."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    data = cache.get_stats()

    # Main stats table
    console.print(stats_table(data))

    # File type distribution
    if data["type_distribution"]:
        type_table = Table(title="File Type Distribution")
        type_table.add_column("Type (UTI)", style="cyan")
        type_table.add_column("Count", justify="right")

        for uti, count in list(data["type_distribution"].items())[:15]:
            type_table.add_row(uti, format_count(count))

        console.print(type_table)

    # Health summary
    console.print("\n[bold]Health Summary:[/bold]")
    total = data["total"] or 1
    gps_pct = (data["with_gps"] / total) * 100
    date_missing_pct = (data["no_date"] / total) * 100

    if date_missing_pct > 5:
        console.print(f"  [yellow]! {format_count(data['no_date'])} items missing dates ({date_missing_pct:.1f}%)[/yellow]")
    else:
        console.print(f"  [green]Dates: {100 - date_missing_pct:.1f}% coverage[/green]")

    console.print(f"  GPS: {gps_pct:.1f}% of items have location data")
    console.print(f"  Screenshots: {format_count(data['screenshots'])} ({data['screenshots'] / total * 100:.1f}%)")

    last_scan = cache.get_scan_meta("last_scan")
    if last_scan:
        console.print(f"\n[dim]Last scan: {last_scan[:19]}[/dim]")

    cache.close()
