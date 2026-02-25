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
    no_transfer_meta: Annotated[
        bool,
        typer.Option("--no-transfer-meta", help="Skip metadata transfers when applying."),
    ] = False,
) -> None:
    """Review and apply pending actions."""
    config = get_config()
    cache = CacheDB(config.db_path)

    if clear:
        cache.clear_pending_actions()
        cache.clear_pending_transfers()
        console.print("[green]All pending actions and transfers cleared.[/green]")
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

        # Show pending metadata transfers
        pending_transfers = cache.get_pending_transfers()
        if pending_transfers:
            transfer_table = Table(title="Pending Metadata Transfers")
            transfer_table.add_column("Keeper UUID", style="dim", max_width=12)
            transfer_table.add_column("Group")
            transfer_table.add_column("Date Transfer", style="green")
            transfer_table.add_column("GPS Transfer", style="blue")
            transfer_table.add_column("Source UUID", style="dim", max_width=12)

            for t in pending_transfers[:50]:
                date_str = t.transfer_date.isoformat() if t.transfer_date else "—"
                gps_str = (
                    f"{t.transfer_latitude:.4f}, {t.transfer_longitude:.4f}"
                    if t.transfer_latitude is not None
                    else "—"
                )
                transfer_table.add_row(
                    t.keeper_uuid[:12],
                    str(t.group_id),
                    date_str,
                    gps_str,
                    t.source_uuid[:12] if t.source_uuid else "—",
                )

            console.print(transfer_table)
            if len(pending_transfers) > 50:
                console.print(f"  [dim]... and {len(pending_transfers) - 50} more[/dim]")

    if apply:
        deletes = [a for a in pending if a.action == ActionType.DELETE]
        if not deletes:
            console.print("[yellow]No items to delete.[/yellow]")
            cache.close()
            return

        # Apply metadata transfers before creating the deletion album
        if not no_transfer_meta:
            pending_transfers = cache.get_pending_transfers()
            if pending_transfers:
                from media_scanner.actions.photokit import update_metadata_photokit

                payload = []
                for t in pending_transfers:
                    entry: dict = {"uuid": t.keeper_uuid}
                    if t.transfer_date:
                        entry["date"] = t.transfer_date.isoformat()
                    if t.transfer_latitude is not None and t.transfer_longitude is not None:
                        entry["latitude"] = t.transfer_latitude
                        entry["longitude"] = t.transfer_longitude
                    payload.append(entry)

                console.print(
                    f"[bold]Applying {len(pending_transfers)} metadata transfer(s)...[/bold]"
                )
                result = update_metadata_photokit(payload)
                if result["success_count"] > 0:
                    console.print(
                        f"  [green]{result['success_count']} metadata transfer(s) applied.[/green]"
                    )
                if result["error_count"] > 0:
                    console.print(
                        f"  [yellow]{result['error_count']} transfer(s) failed "
                        f"(proceeding with album creation).[/yellow]"
                    )
                    for err in result.get("errors", []):
                        console.print(f"    [dim]{err}[/dim]")

                # Mark transfers as applied
                for t in pending_transfers:
                    error_msg = None
                    for err in result.get("errors", []):
                        if err.startswith(t.keeper_uuid):
                            error_msg = err
                            break
                    cache.mark_transfer_applied(t.keeper_uuid, t.group_id, error=error_msg)

        uuids = [a.uuid for a in deletes]
        total = len(uuids)

        import threading
        from pathlib import Path
        from rich.progress import (
            Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
        )
        from media_scanner.actions.photokit import create_deletion_album_photokit
        from media_scanner.actions.applescript import ALBUM_NAME

        progress_file = Path.home() / ".media-scanner" / "bridge-progress.tmp"
        progress_file.unlink(missing_ok=True)

        console.print(
            f"\n[bold]Creating '{ALBUM_NAME}' album with "
            f"{format_count(total)} items...[/bold]"
        )

        result_box: list = [None]

        def _run_photokit():
            result_box[0] = create_deletion_album_photokit(
                uuids, ALBUM_NAME, progress_file=progress_file,
            )

        with Progress(
            SpinnerColumn(),
            BarColumn(),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Adding", total=total)

            thread = threading.Thread(target=_run_photokit)
            thread.start()

            while thread.is_alive():
                if progress_file.exists():
                    try:
                        val = int(progress_file.read_text().strip())
                        progress.update(task, completed=val)
                    except (ValueError, OSError):
                        pass
                thread.join(timeout=0.3)

            # Final update
            progress.update(task, completed=total)

        progress_file.unlink(missing_ok=True)

        pk_result = result_box[0]
        success = pk_result["success"]
        if not success:
            if pk_result["error"] == "auth_denied":
                console.print(
                    "[yellow]PhotoKit access denied. Grant Photos access:[/yellow]\n"
                    "  [bold]System Settings → Privacy & Security → Photos → "
                    "toggle PhotosBridge ON[/bold]\n"
                    "[yellow]Falling back to AppleScript (slower)...[/yellow]"
                )
            else:
                console.print(
                    f"[yellow]PhotoKit unavailable ({pk_result['error']}), "
                    f"falling back to AppleScript...[/yellow]"
                )
            from media_scanner.actions.applescript import create_deletion_album

            with Progress(
                SpinnerColumn(),
                BarColumn(),
                TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Adding (AppleScript)", total=total)

                def _as_progress(done: int):
                    progress.update(task, completed=done)

                success = create_deletion_album(
                    uuids, progress_callback=_as_progress,
                )

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
