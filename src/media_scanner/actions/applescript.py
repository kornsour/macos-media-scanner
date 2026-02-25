"""Photos.app bridge via AppleScript - create albums for manual deletion."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


ALBUM_NAME = "Media Scanner - To Delete"


def _run_applescript(script: str, timeout: int = 600) -> bool:
    """Write an AppleScript to a temp file and execute it.

    Using a temp file avoids the OS argument-length limit that occurs when
    passing large scripts via ``osascript -e``.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".scpt", delete=False
    ) as f:
        f.write(script)
        f.flush()
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            ["osascript", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


def _ensure_album_exists() -> bool:
    """Create the deletion album if it doesn't already exist. Returns True on success."""
    script = f'''
    tell application "Photos"
        set albumName to "{ALBUM_NAME}"
        set targetAlbum to missing value
        repeat with a in albums
            if name of a is albumName then
                set targetAlbum to a
                exit repeat
            end if
        end repeat
        if targetAlbum is missing value then
            make new album named albumName
        end if
    end tell
    '''
    return _run_applescript(script, timeout=60)


def _add_batch_to_album(uuids: list[str]) -> bool:
    """Add a batch of UUIDs to the existing deletion album."""
    uuid_list = ", ".join(f'"{u}"' for u in uuids)

    script = f'''
    tell application "Photos"
        set albumName to "{ALBUM_NAME}"
        set targetAlbum to missing value
        repeat with a in albums
            if name of a is albumName then
                set targetAlbum to a
                exit repeat
            end if
        end repeat

        if targetAlbum is missing value then
            return "error: album not found"
        end if

        set uuidList to {{{uuid_list}}}
        set itemsToAdd to {{}}

        repeat with mediaItem in media items
            if id of mediaItem is in uuidList then
                set end of itemsToAdd to mediaItem
            end if
        end repeat

        if (count of itemsToAdd) > 0 then
            add itemsToAdd to targetAlbum
        end if

        return (count of itemsToAdd) as text
    end tell
    '''
    return _run_applescript(script, timeout=600)


def create_deletion_album(uuids: list[str], batch_size: int = 500) -> bool:
    """Create (or update) an album in Photos.app containing the items to delete.

    Processes UUIDs in batches via temp-file AppleScript to avoid OS arg limits.
    Returns True on success.
    """
    if not uuids:
        return True

    if not _ensure_album_exists():
        return False

    for i in range(0, len(uuids), batch_size):
        batch = uuids[i : i + batch_size]
        if not _add_batch_to_album(batch):
            return False

    return True
