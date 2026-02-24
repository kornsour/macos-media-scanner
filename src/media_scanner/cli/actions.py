"""actions command - apply decisions (create albums, export)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from media_scanner.cli.app import get_config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionType
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count


def actions(
    list_pending: Annotated[
        bool,
        typer.Option("--list", help="List pending actions."),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply pending actions (create album in Photos.app)."),
    ] = False,
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Clear all pending actions."),
    ] = False,
    export_path: Annotated[
        str,
        typer.Option("--export", help="Export keepers to a directory."),
    ] = "",
) -> None:
    """Review and apply pending actions."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if clear:
        cache.clear_pending_actions()
        console.print("[green]All pending actions cleared.[/green]")
        cache.close()
        return

    pending = cache.get_pending_actions()

    if not pending and not list_pending and not apply:
        console.print("[yellow]No pending actions. Use --list, --apply, or --clear.[/yellow]")
        cache.close()
        return

    if list_pending or (not apply and not export_path):
        deletes = [a for a in pending if a.action == ActionType.DELETE]
        keeps = [a for a in pending if a.action == ActionType.KEEP]

        console.print(f"\n[bold]Pending Actions:[/bold]")
        console.print(f"  Delete: {format_count(len(deletes))} items")
        console.print(f"  Keep:   {format_count(len(keeps))} items")

        if deletes:
            table = Table(title="Items Marked for Deletion")
            table.add_column("UUID", style="dim", max_width=12)
            table.add_column("Filename", style="cyan")
            table.add_column("Group")

            for action in deletes[:50]:
                item = cache.get_item(action.uuid)
                filename = item.filename if item else "?"
                table.add_row(
                    action.uuid[:12],
                    filename,
                    str(action.group_id) if action.group_id else "—",
                )

            console.print(table)
            if len(deletes) > 50:
                console.print(f"  [dim]... and {len(deletes) - 50} more[/dim]")

    if apply:
        deletes = [a for a in pending if a.action == ActionType.DELETE]
        if not deletes:
            console.print("[yellow]No items to delete.[/yellow]")
            cache.close()
            return

        console.print(
            f"\n[bold]Creating 'Media Scanner - To Delete' album with "
            f"{format_count(len(deletes))} items...[/bold]"
        )

        uuids = [a.uuid for a in deletes]

        from media_scanner.actions.photokit import create_deletion_album_photokit
        from media_scanner.actions.applescript import ALBUM_NAME
        success = create_deletion_album_photokit(uuids, ALBUM_NAME)
        if not success:
            console.print("[yellow]PhotoKit unavailable, falling back to AppleScript...[/yellow]")
            from media_scanner.actions.applescript import create_deletion_album
            success = create_deletion_album(uuids)

        if success:
            cache.mark_actions_applied(uuids)
            console.print(
                "[green]Album created! Open Photos.app, review the album, "
                "and delete items manually.[/green]"
            )
        else:
            console.print("[red]Failed to create album. Check Photos.app permissions.[/red]")

    if export_path:
        from pathlib import Path
        from media_scanner.actions.exporter import export_keepers

        keeps = [a for a in pending if a.action == ActionType.KEEP]
        if not keeps:
            console.print("[yellow]No items marked as keepers to export.[/yellow]")
            cache.close()
            return

        items = []
        for action in keeps:
            item = cache.get_item(action.uuid)
            if item and item.path:
                items.append(item)

        console.print(f"Exporting {format_count(len(items))} keepers to {export_path}...")
        exported = export_keepers(items, Path(export_path))
        console.print(f"[green]Exported {format_count(exported)} files.[/green]")

    cache.close()
