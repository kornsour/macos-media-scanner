"""small-photos command - find small photos and add them to a Photos album."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaType
from media_scanner.ui.console import console


SMALL_PHOTOS_ALBUM = "Media Scanner - Small Photos"


def small_photos(
    max_size: Annotated[
        float,
        typer.Option("--max-kb", help="Maximum file size in KB (inclusive)."),
    ] = 15.0,
    album: Annotated[
        str,
        typer.Option("--album", help="Album name to add photos to."),
    ] = SMALL_PHOTOS_ALBUM,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List matching photos without creating an album."),
    ] = False,
) -> None:
    """Find small photos (<=15KB by default) and add them to a Photos album."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    max_bytes = int(max_size * 1024)
    all_items = cache.get_all_items()
    matches = [
        i for i in all_items
        if i.media_type in (MediaType.PHOTO, MediaType.LIVE_PHOTO)
        and i.file_size <= max_bytes
    ]

    if not matches:
        console.print(
            f"[green]No photos found at or below {max_size:.0f} KB.[/green]"
        )
        cache.close()
        return

    console.print(
        f"Found [cyan]{len(matches)}[/cyan] photos at or below {max_size:.0f} KB."
    )

    # Show a summary table
    for item in matches[:20]:
        console.print(
            f"  {item.filename}  "
            f"[dim]{item.file_size / 1024:.1f} KB  "
            f"{item.width}x{item.height}[/dim]"
        )
    if len(matches) > 20:
        console.print(f"  [dim]... and {len(matches) - 20} more[/dim]")

    if dry_run:
        console.print("\n[dim]Dry run — no album created.[/dim]")
        cache.close()
        return

    # Add to Photos album via PhotoKit
    uuids = [item.uuid for item in matches]
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

        # Fallback to AppleScript
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
