"""Action queue management - wrapper around cache DB actions."""

from __future__ import annotations

import threading
from pathlib import Path

from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
)

from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionRecord, ActionType
from media_scanner.ui.console import console
from media_scanner.ui.formatters import format_count


def get_action_summary(cache: CacheDB) -> dict[str, int]:
    """Get a summary of pending actions by type."""
    pending = cache.get_pending_actions()
    summary: dict[str, int] = {}
    for action in pending:
        key = action.action.value
        summary[key] = summary.get(key, 0) + 1
    return summary


def get_delete_uuids(cache: CacheDB) -> list[str]:
    """Get UUIDs of items marked for deletion."""
    pending = cache.get_pending_actions(action_type=ActionType.DELETE)
    return [a.uuid for a in pending]


def undo_group_actions(cache: CacheDB, group_id: int) -> int:
    """Remove all pending actions for a specific duplicate group.

    Returns the number of actions removed.
    """
    pending = cache.get_pending_actions()
    to_remove = [a for a in pending if a.group_id == group_id]
    # Clear and re-add everything except the target group
    remaining = [a for a in pending if a.group_id != group_id]
    cache.clear_pending_actions()
    for action in remaining:
        cache.save_action(action)
    return len(to_remove)


def apply_pending_actions(cache: CacheDB, *, transfer_meta: bool = True) -> bool:
    """Apply all pending actions: metadata transfers, deletion album, keepers album.

    Returns True if the album was created successfully, False otherwise.
    """
    from media_scanner.actions.photokit import (
        create_deletion_album_photokit,
        update_metadata_photokit,
    )
    from media_scanner.actions.applescript import ALBUM_NAME, KEEPER_ALBUM_NAME

    pending = cache.get_pending_actions()
    deletes = [a for a in pending if a.action == ActionType.DELETE]

    if not deletes:
        console.print("[yellow]No items to delete.[/yellow]")
        return False

    # Apply metadata transfers before creating the deletion album
    if transfer_meta:
        pending_transfers = cache.get_pending_transfers()
        if pending_transfers:
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
        # Also add keepers to the keepers album
        keeps = [a for a in pending if a.action == ActionType.KEEP]
        if keeps:
            keeper_uuids = [a.uuid for a in keeps]
            keeper_result = create_deletion_album_photokit(
                keeper_uuids, KEEPER_ALBUM_NAME
            )
            if keeper_result["success"]:
                console.print(
                    f"  [green]{len(keeper_uuids)} keeper(s) added to "
                    f"'{KEEPER_ALBUM_NAME}' album.[/green]"
                )
            else:
                console.print(
                    f"  [yellow]Could not add keepers to album: "
                    f"{keeper_result.get('error', 'unknown')}[/yellow]"
                )

        cache.mark_actions_applied(uuids)
        console.print(
            "[green]Album created! Open Photos.app, review the album, "
            "and delete items manually.[/green]"
        )
    else:
        console.print("[red]Failed to create album. Check Photos.app permissions.[/red]")

    return success
