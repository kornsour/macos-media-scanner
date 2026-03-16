"""similar command - find visually similar (not identical) photos."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.auto_resolver import auto_resolve
from media_scanner.core.metadata_merger import compute_transfers
from media_scanner.core.quality_scorer import rank_group
from media_scanner.core.similar_finder import find_similar_photos
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionType
from media_scanner.ui.console import console
from media_scanner.ui.progress import create_progress
from media_scanner.ui.reviewer import ReviewSession


def similar(
    auto: Annotated[
        bool,
        typer.Option("--auto", help="Auto-accept all quality-scorer recommendations."),
    ] = False,
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
        rank_group(group, config, cache=cache)

    if limit > 0:
        groups = groups[:limit]

    actions = []

    if auto:
        actions = auto_resolve(groups, config)
        for action in actions:
            cache.save_action(action)

        deletes = sum(1 for a in actions if a.action == ActionType.DELETE)
        keeps = sum(1 for a in actions if a.action == ActionType.KEEP)
        console.print(f"\n[bold]Auto-resolved: {keeps} keep, {deletes} delete.[/bold]")
        if deletes:
            console.print(
                "Run [cyan]media-scanner actions --list[/cyan] to review, "
                "or [cyan]media-scanner actions --apply[/cyan] to create the deletion album."
            )
    elif review:
        session = ReviewSession(groups, config)
        actions = session.run()
        for action in actions:
            cache.save_action(action)

    # Compute and save metadata transfers
    if actions:
        items_by_uuid = {item.uuid: item for g in groups for item in g.items}
        transfers = compute_transfers(actions, items_by_uuid)
        for transfer in transfers:
            cache.save_metadata_transfer(transfer)
        if transfers:
            console.print(
                f"  [dim]{len(transfers)} metadata transfer(s) queued "
                f"(date/GPS from duplicates to keepers).[/dim]"
            )

    cache.close()
