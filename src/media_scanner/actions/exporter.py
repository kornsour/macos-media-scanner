"""Export keeper files to filesystem."""

from __future__ import annotations

import shutil
from pathlib import Path

from media_scanner.data.models import MediaItem


def export_keepers(items: list[MediaItem], dest_dir: Path) -> int:
    """Copy keeper files to a destination directory.

    Returns the number of files successfully exported.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    exported = 0

    for item in items:
        if not item.path or not item.path.exists():
            continue

        dest_file = dest_dir / item.filename
        # Handle name collisions
        if dest_file.exists():
            stem = dest_file.stem
            suffix = dest_file.suffix
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.copy2(item.path, dest_file)
            exported += 1
        except (OSError, PermissionError):
            continue

    return exported
