"""Data models for media-scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class MediaType(Enum):
    PHOTO = "photo"
    VIDEO = "video"
    LIVE_PHOTO = "live_photo"
    OTHER = "other"


class MatchType(Enum):
    EXACT = "exact"
    NEAR = "near"
    SIMILAR = "similar"


class ActionType(Enum):
    DELETE = "delete"
    KEEP = "keep"
    EXPORT = "export"
    SKIP = "skip"


@dataclass
class MediaItem:
    """Represents a single photo or video from the Photos library."""

    uuid: str
    filename: str
    original_filename: str
    path: Path | None  # path to the file on disk (may be None for cloud-only)
    media_type: MediaType
    file_size: int  # bytes
    width: int
    height: int
    date_created: datetime | None
    date_modified: datetime | None
    duration: float | None  # seconds, for video
    uti: str  # uniform type identifier (e.g. public.heic)
    has_gps: bool
    latitude: float | None
    longitude: float | None
    albums: list[str] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    is_edited: bool = False
    is_favorite: bool = False
    is_hidden: bool = False
    is_screenshot: bool = False
    is_selfie: bool = False
    is_burst: bool = False
    burst_uuid: str | None = None
    live_photo_uuid: str | None = None
    live_photo_video_path: Path | None = None  # path to .mov component of live photo
    apple_score: float | None = None  # overall aesthetic score 0-1
    sha256: str | None = None
    dhash: str | None = None
    phash: str | None = None


@dataclass
class DuplicateGroup:
    """A group of items detected as duplicates."""

    group_id: int
    match_type: MatchType
    items: list[MediaItem] = field(default_factory=list)
    recommended_keep_uuid: str | None = None  # uuid of the best item


@dataclass
class ActionRecord:
    """A pending action on a media item."""

    uuid: str
    action: ActionType
    group_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    applied: bool = False
    applied_at: datetime | None = None


@dataclass
class MetadataTransfer:
    """A pending metadata transfer from a duplicate to the keeper."""

    keeper_uuid: str
    group_id: int
    transfer_date: datetime | None = None
    transfer_latitude: float | None = None
    transfer_longitude: float | None = None
    source_uuid: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    applied: bool = False
    applied_at: datetime | None = None
    error_message: str | None = None
