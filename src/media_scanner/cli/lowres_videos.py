"""lowres-videos command - find low-resolution videos and add them to a Photos album."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaType
from media_scanner.ui.console import console


LOWRES_VIDEO_ALBUM = "Media Scanner - Low Res Videos"


def lowres_videos(
    max_height: Annotated[
        int,
        typer.Option("--max-height", help="Maximum vertical resolution (inclusive)."),
    ] = 720,
    album: Annotated[
        str,
        typer.Option("--album", help="Album name to add videos to."),
    ] = LOWRES_VIDEO_ALBUM,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List matching videos without creating an album."),
    ] = False,
) -> None:
    """Find low-resolution videos (<=720p by default) and add them to a Photos album."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    all_items = cache.get_all_items()
    matches = [
        i for i in all_items
        if i.media_type in (MediaType.VIDEO, MediaType.LIVE_PHOTO)
        and min(i.width, i.height) <= max_height
        and i.width > 0 and i.height > 0
    ]

    if not matches:
        console.print(
            f"[green]No videos found at or below {max_height}p.[/green]"
        )
        cache.close()
        return

    console.print(
        f"Found [cyan]{len(matches)}[/cyan] videos at or below {max_height}p."
    )

    for item in matches[:20]:
        console.print(
            f"  {item.filename}  "
            f"[dim]{item.width}x{item.height}  "
            f"{item.duration or 0:.1f}s  "
            f"{item.file_size / 1024 / 1024:.1f} MB[/dim]"
        )
    if len(matches) > 20:
        console.print(f"  [dim]... and {len(matches) - 20} more[/dim]")

    if dry_run:
        console.print("\n[dim]Dry run — no album created.[/dim]")
        cache.close()
        return

    uuids = [item.uuid for item in matches]
    console.print(f'\n[bold]Adding {len(uuids)} videos to album "{album}"...[/bold]')

    from media_scanner.actions.photokit import create_deletion_album_photokit

    result = create_deletion_album_photokit(uuids, album_name=album)
    if result["success"]:
        console.print(
            f'[green]Done! {len(uuids)} videos added to "{album}" in Photos.app.[/green]'
        )
    else:
        error = result.get("error", "unknown error")
        console.print(f"[red]PhotoKit failed: {error}[/red]")

        console.print("[dim]Trying AppleScript fallback...[/dim]")
        from media_scanner.actions.applescript import create_deletion_album

        success = create_deletion_album(uuids, album_name_override=album)
        if success:
            console.print(
                f'[green]Done! {len(uuids)} videos added to "{album}" via AppleScript.[/green]'
            )
        else:
            console.print("[red]AppleScript fallback also failed.[/red]")

    cache.close()
