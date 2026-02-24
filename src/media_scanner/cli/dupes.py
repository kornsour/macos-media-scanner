"""dupes command - find and review duplicate photos/videos."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.core.auto_resolver import auto_resolve
from media_scanner.core.duplicate_finder import (
    find_exact_duplicates,
    find_near_duplicates,
    find_video_duplicates,
)
from media_scanner.core.metadata_merger import compute_transfers
from media_scanner.core.quality_scorer import rank_group
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionType, MatchType
from media_scanner.ui.console import console
from media_scanner.ui.progress import create_progress
from media_scanner.ui.reviewer import ReviewSession


def dupes(
    exact: Annotated[
        bool,
        typer.Option("--exact", help="Find exact duplicates only (SHA-256)."),
    ] = False,
    near: Annotated[
        bool,
        typer.Option("--near", help="Find near-duplicates (perceptual hashing)."),
    ] = False,
    videos: Annotated[
        bool,
        typer.Option("--videos", help="Include video duplicate detection."),
    ] = False,
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
        typer.Option("--limit", help="Max groups to show/review."),
    ] = 0,
) -> None:
    """Find duplicate photos and videos."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    # Default: find exact if neither flag is set
    if not exact and not near:
        exact = True

    all_groups = []

    if exact:
        console.print("[bold]Finding exact duplicates (size + SHA-256)...[/bold]")
        with create_progress() as progress:
            task = progress.add_task("Hashing", total=100)

            def exact_progress(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            groups = find_exact_duplicates(cache, progress_callback=exact_progress)

        console.print(f"  Found [cyan]{len(groups)}[/cyan] exact duplicate groups.")
        all_groups.extend(groups)

    if near:
        console.print("[bold]Finding near-duplicates (perceptual hashing)...[/bold]")
        with create_progress() as progress:
            task = progress.add_task("Hashing", total=100)

            def near_progress(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            groups = find_near_duplicates(cache, config, progress_callback=near_progress)

        console.print(f"  Found [cyan]{len(groups)}[/cyan] near-duplicate groups.")
        all_groups.extend(groups)

    if videos:
        console.print("[bold]Finding video duplicates...[/bold]")
        with create_progress() as progress:
            task = progress.add_task("Processing videos", total=100)

            def video_progress(done: int, total: int) -> None:
                progress.update(task, completed=done, total=total)

            groups = find_video_duplicates(cache, config, progress_callback=video_progress)

        console.print(f"  Found [cyan]{len(groups)}[/cyan] video duplicate groups.")
        all_groups.extend(groups)

    if not all_groups:
        console.print("[green]No duplicates found![/green]")
        cache.close()
        return

    # Rank all groups
    for group in all_groups:
        rank_group(group, config)

    # Save groups to cache
    cache.clear_duplicate_groups()
    for group in all_groups:
        cache.save_duplicate_group(group)

    total_dupes = sum(len(g.items) - 1 for g in all_groups)
    console.print(
        f"\n[bold]Total: {len(all_groups)} groups, {total_dupes} potential duplicates to review.[/bold]"
    )

    if limit > 0:
        all_groups = all_groups[:limit]

    actions = []

    if auto:
        actions = auto_resolve(all_groups, config)
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
        session = ReviewSession(all_groups, config)
        actions = session.run()

        # Save actions
        for action in actions:
            cache.save_action(action)

        deletes = sum(1 for a in actions if a.action == ActionType.DELETE)
        keeps = sum(1 for a in actions if a.action == ActionType.KEEP)
        console.print(f"\n[bold]Decisions: {keeps} keep, {deletes} delete.[/bold]")
        if deletes:
            console.print(
                "Run [cyan]media-scanner actions --list[/cyan] to review, "
                "or [cyan]media-scanner actions --apply[/cyan] to create the deletion album."
            )

    # Compute and save metadata transfers
    if actions:
        items_by_uuid = {item.uuid: item for g in all_groups for item in g.items}
        transfers = compute_transfers(actions, items_by_uuid)
        for transfer in transfers:
            cache.save_metadata_transfer(transfer)
        if transfers:
            console.print(
                f"  [dim]{len(transfers)} metadata transfer(s) queued "
                f"(date/GPS from duplicates to keepers).[/dim]"
            )

    cache.close()
