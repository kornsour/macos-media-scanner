"""osxphotos integration - the only module that imports osxphotos."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import osxphotos

from media_scanner.data.models import MediaItem, MediaType

if TYPE_CHECKING:
    from collections.abc import Iterator


def _classify_media_type(photo: osxphotos.PhotoInfo) -> MediaType:
    if photo.ismovie:
        return MediaType.VIDEO
    if photo.live_photo:
        return MediaType.LIVE_PHOTO
    return MediaType.PHOTO


def _safe_path(photo: osxphotos.PhotoInfo) -> Path | None:
    """Get the on-disk path, returning None if unavailable (cloud-only)."""
    try:
        p = photo.path
        return Path(p) if p else None
    except Exception:
        return None


def _get_albums(photo: osxphotos.PhotoInfo) -> list[str]:
    try:
        return list(photo.albums) if photo.albums else []
    except Exception:
        return []


def _get_persons(photo: osxphotos.PhotoInfo) -> list[str]:
    try:
        return list(photo.persons) if photo.persons else []
    except Exception:
        return []


def _get_keywords(photo: osxphotos.PhotoInfo) -> list[str]:
    try:
        return list(photo.keywords) if photo.keywords else []
    except Exception:
        return []


def _get_score(photo: osxphotos.PhotoInfo) -> float | None:
    try:
        score_info = photo.score
        if score_info:
            return score_info.overall
    except Exception:
        pass
    return None


def photo_to_media_item(photo: osxphotos.PhotoInfo) -> MediaItem:
    """Convert an osxphotos PhotoInfo to our MediaItem model."""
    location = photo.location if photo.location else (None, None)
    has_gps = location[0] is not None and location[1] is not None

    return MediaItem(
        uuid=photo.uuid,
        filename=photo.filename or "",
        original_filename=photo.original_filename or photo.filename or "",
        path=_safe_path(photo),
        media_type=_classify_media_type(photo),
        file_size=photo.original_filesize or 0,
        width=photo.width or 0,
        height=photo.height or 0,
        date_created=photo.date if photo.date else None,
        date_modified=photo.date_modified if photo.date_modified else None,
        duration=photo.duration if photo.ismovie else None,
        uti=photo.uti or "",
        has_gps=has_gps,
        latitude=location[0],
        longitude=location[1],
        albums=_get_albums(photo),
        persons=_get_persons(photo),
        keywords=_get_keywords(photo),
        is_edited=bool(photo.hasadjustments),
        is_favorite=bool(photo.favorite),
        is_hidden=bool(photo.hidden),
        is_screenshot=bool(photo.screenshot),
        is_selfie=bool(photo.selfie),
        is_burst=bool(photo.burst),
        burst_uuid=photo.burst_photos[0].uuid if photo.burst and photo.burst_photos else None,
        live_photo_uuid=None,
        apple_score=_get_score(photo),
    )


def scan_library(
    library_path: Path | None = None,
) -> Iterator[MediaItem]:
    """Iterate over all items in the Photos library, yielding MediaItems."""
    if library_path:
        photosdb = osxphotos.PhotosDB(dbfile=str(library_path))
    else:
        photosdb = osxphotos.PhotosDB()

    for photo in photosdb.photos():
        try:
            yield photo_to_media_item(photo)
        except Exception:
            # Skip items that can't be converted
            continue


def get_library_info(library_path: Path | None = None) -> dict:
    """Get basic info about the Photos library."""
    if library_path:
        photosdb = osxphotos.PhotosDB(dbfile=str(library_path))
    else:
        photosdb = osxphotos.PhotosDB()
    return {
        "db_path": photosdb.db_path,
        "db_version": photosdb.db_version,
        "photo_count": len(photosdb.photos()),
    }
