"""Photos.app bridge via AppleScript - create albums for manual deletion."""

from __future__ import annotations

import subprocess


ALBUM_NAME = "Media Scanner - To Delete"


def create_deletion_album(uuids: list[str]) -> bool:
    """Create (or update) an album in Photos.app containing the items to delete.

    Uses AppleScript via osascript. Returns True on success.
    """
    if not uuids:
        return True

    # Build the UUID list for AppleScript
    uuid_list = ", ".join(f'"{u}"' for u in uuids)

    script = f'''
    tell application "Photos"
        -- Create or get existing album
        set albumName to "{ALBUM_NAME}"
        set targetAlbum to missing value

        repeat with a in albums
            if name of a is albumName then
                set targetAlbum to a
                exit repeat
            end if
        end repeat

        if targetAlbum is missing value then
            set targetAlbum to make new album named albumName
        end if

        -- Find media items by UUID and add to album
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

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes for large libraries
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def create_album_batch(uuids: list[str], batch_size: int = 100) -> bool:
    """Create the deletion album in batches to avoid AppleScript limits.

    For very large libraries, process UUIDs in chunks.
    """
    for i in range(0, len(uuids), batch_size):
        batch = uuids[i:i + batch_size]
        if not create_deletion_album(batch):
            return False
    return True
