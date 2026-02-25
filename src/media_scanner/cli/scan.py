"""scan command - read Photos library and cache metadata."""

from __future__ import annotations

from datetime import datetime

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.scanner import scan_library
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count
from media_scanner.ui.progress import create_scan_progress


def scan() -> None:
    """Read the Photos library and cache metadata in SQLite."""
    config = get_config()
    cache = CacheDB(config.db_path)

    console.print("[bold]Scanning Photos library...[/bold]")
    console.print(f"Cache: {config.db_path}")

    batch: list = []
    scanned_uuids: set[str] = set()
    batch_size = 500
    total = 0

    with create_scan_progress() as progress:
        task = progress.add_task("Scanning", total=None)

        for item in scan_library(config.photos_library):
            batch.append(item)
            scanned_uuids.add(item.uuid)
            total += 1

            if len(batch) >= batch_size:
                cache.upsert_items_batch(batch)
                batch.clear()

            progress.update(task, completed=total, description=f"Scanning ({format_count(total)} items)")

        # Flush remaining
        if batch:
            cache.upsert_items_batch(batch)

    # Remove items that no longer exist in the Photos library
    removed = cache.remove_items_not_in(scanned_uuids)

    cache.set_scan_meta("last_scan", datetime.now().isoformat())
    cache.set_scan_meta("item_count", str(total))

    console.print(f"\n[green]Scan complete. {format_count(total)} items cached.[/green]")
    if removed:
        console.print(f"  [dim]{format_count(removed)} stale item(s) pruned from cache.[/dim]")
    cache.close()
