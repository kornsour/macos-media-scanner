"""short-videos command - find short videos and add them to a Photos album."""

from __future__ import annotations

from typing import Annotated

import typer

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaType
from media_scanner.ui.console import console


SHORT_VIDEO_ALBUM = "Media Scanner - Short Videos"


def short_videos(
    min_duration: Annotated[
        float,
        typer.Option("--min", help="Minimum duration in seconds (inclusive)."),
    ] = 1.0,
    max_duration: Annotated[
        float,
        typer.Option("--max", help="Maximum duration in seconds (inclusive)."),
    ] = 3.0,
    album: Annotated[
        str,
        typer.Option("--album", help="Album name to add videos to."),
    ] = SHORT_VIDEO_ALBUM,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List matching videos without creating an album."),
    ] = False,
) -> None:
    """Find short videos (1-3s by default) and add them to a Photos album."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if cache.item_count() == 0:
        console.print("[red]No items in cache. Run 'media-scanner scan' first.[/red]")
        raise typer.Exit(1)

    all_items = cache.get_all_items()
    matches = [
        i for i in all_items
        if i.media_type in (MediaType.VIDEO, MediaType.LIVE_PHOTO)
        and i.duration is not None
        and min_duration <= i.duration <= max_duration
    ]

    if not matches:
        console.print(
            f"[green]No videos found with duration {min_duration}-{max_duration}s.[/green]"
        )
        cache.close()
        return

    console.print(
        f"Found [cyan]{len(matches)}[/cyan] videos with duration "
        f"{min_duration}-{max_duration}s."
    )

    # Show a summary table
    for item in matches[:20]:
        console.print(
            f"  {item.filename}  "
            f"[dim]{item.duration:.1f}s  "
            f"{item.file_size / 1024:.0f} KB[/dim]"
        )
    if len(matches) > 20:
        console.print(f"  [dim]... and {len(matches) - 20} more[/dim]")

    if dry_run:
        console.print("\n[dim]Dry run — no album created.[/dim]")
        cache.close()
        return

    # Add to Photos album via PhotoKit
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

        # Fallback to AppleScript
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
