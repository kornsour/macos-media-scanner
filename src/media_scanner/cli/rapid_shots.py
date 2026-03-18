"""rapid-shots command - find photos taken in quick succession and add them to a Photos album."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaType
from media_scanner.ui.console import console


RAPID_SHOTS_ALBUM = "Media Scanner - Rapid Shots"


def _find_rapid_sequences(
    items: list,
    max_gap: float,
    min_burst: int,
) -> list[list]:
    """Group items into sequences where consecutive shots are <= max_gap seconds apart.

    Only returns sequences with at least *min_burst* items.
    Items must be pre-sorted by date_created ascending.
    """
    sequences: list[list] = []
    current: list = [items[0]]

    for item in items[1:]:
        gap = (item.date_created - current[-1].date_created).total_seconds()
        if gap <= max_gap:
            current.append(item)
        else:
            if len(current) >= min_burst:
                sequences.append(current)
            current = [item]

    if len(current) >= min_burst:
        sequences.append(current)

    return sequences


def rapid_shots(
    max_gap: Annotated[
        float,
        typer.Option("--gap", help="Max seconds between consecutive shots to be considered a sequence."),
    ] = 3.0,
    min_burst: Annotated[
        int,
        typer.Option("--min-burst", help="Minimum number of photos in a sequence."),
    ] = 3,
    album: Annotated[
        str,
        typer.Option("--album", help="Album name to add photos to."),
    ] = RAPID_SHOTS_ALBUM,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List matching sequences without creating an album."),
    ] = False,
) -> None:
    """Find photos taken in quick succession and add them to a Photos album."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    all_items = cache.get_all_items()
    photos = [
        i for i in all_items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.date_created is not None
    ]
    photos.sort(key=lambda i: i.date_created)

    if len(photos) < min_burst:
        console.print("[green]Not enough photos to detect rapid sequences.[/green]")
        cache.close()
        return

    sequences = _find_rapid_sequences(photos, max_gap, min_burst)

    if not sequences:
        console.print(
            f"[green]No sequences of {min_burst}+ photos within {max_gap}s found.[/green]"
        )
        cache.close()
        return

    total_photos = sum(len(s) for s in sequences)
    console.print(
        f"Found [cyan]{len(sequences)}[/cyan] rapid-shot sequences "
        f"([cyan]{total_photos}[/cyan] photos total)."
    )

    for idx, seq in enumerate(sequences[:15]):
        first = seq[0].date_created.strftime("%Y-%m-%d %H:%M:%S")
        span = (seq[-1].date_created - seq[0].date_created).total_seconds()
        console.print(
            f"  Sequence {idx + 1}: [dim]{len(seq)} photos, "
            f"{first}, span {span:.1f}s[/dim]"
        )
    if len(sequences) > 15:
        console.print(f"  [dim]... and {len(sequences) - 15} more sequences[/dim]")

    if dry_run:
        console.print("\n[dim]Dry run — no album created.[/dim]")
        cache.close()
        return

    uuids = [item.uuid for seq in sequences for item in seq]
    console.print(f'\n[bold]Adding {len(uuids)} photos to album "{album}"...[/bold]')

    from media_scanner.actions.photokit import create_deletion_album_photokit

    result = create_deletion_album_photokit(uuids, album_name=album)
    if result["success"]:
        console.print(
            f'[green]Done! {len(uuids)} photos added to "{album}" in Photos.app.[/green]'
        )
    else:
        error = result.get("error", "unknown error")
        console.print(f"[red]PhotoKit failed: {error}[/red]")

        console.print("[dim]Trying AppleScript fallback...[/dim]")
        from media_scanner.actions.applescript import create_deletion_album

        success = create_deletion_album(uuids, album_name_override=album)
        if success:
            console.print(
                f'[green]Done! {len(uuids)} photos added to "{album}" via AppleScript.[/green]'
            )
        else:
            console.print("[red]AppleScript fallback also failed.[/red]")

    cache.close()
