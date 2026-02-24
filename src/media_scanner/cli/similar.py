"""similar command - find visually similar (not identical) photos."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.quality_scorer import rank_group
from media_scanner.core.similar_finder import find_similar_photos
from media_scanner.data.cache import CacheDB
from media_scanner.ui.console import console
from media_scanner.ui.progress import create_progress
from media_scanner.ui.reviewer import ReviewSession


def similar(
    review: Annotated[
        bool,
        typer.Option("--review/--no-review", help="Start interactive review."),
    ] = True,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max groups to show."),
    ] = 0,
) -> None:
    """Find visually similar (not identical) photos."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    console.print("[bold]Finding similar photos...[/bold]")
    with create_progress() as progress:
        task = progress.add_task("Computing hashes", total=100)

        def cb(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        groups = find_similar_photos(cache, config, progress_callback=cb)

    console.print(f"Found [cyan]{len(groups)}[/cyan] similar photo groups.")

    if not groups:
        cache.close()
        return

    for group in groups:
        rank_group(group, config)

    if limit > 0:
        groups = groups[:limit]

    if review:
        session = ReviewSession(groups, config)
        actions = session.run()
        for action in actions:
            cache.save_action(action)

    cache.close()
