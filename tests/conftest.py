"""Shared fixtures for media-scanner tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from media_scanner.config import Config
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import (
    ActionRecord,
    ActionType,
    DuplicateGroup,
    MatchType,
    MediaItem,
    MediaType,
)


def sample_item(**overrides) -> MediaItem:
    """Build a MediaItem with sensible defaults; override any field via kwargs."""
    defaults = dict(
        uuid="uuid-001",
        filename="IMG_0001.heic",
        original_filename="IMG_0001.heic",
        path=Path("/photos/IMG_0001.heic"),
        media_type=MediaType.PHOTO,
        file_size=2_000_000,
        width=4032,
        height=3024,
        date_created=datetime(2024, 6, 15, 10, 30, 0),
        date_modified=datetime(2024, 6, 15, 10, 35, 0),
        duration=None,
        uti="public.heic",
        has_gps=True,
        latitude=37.7749,
        longitude=-122.4194,
        albums=["Vacation"],
        persons=["Alice"],
        keywords=["beach"],
        is_edited=False,
        is_favorite=False,
        is_hidden=False,
        is_screenshot=False,
        is_selfie=False,
        is_burst=False,
        burst_uuid=None,
        live_photo_uuid=None,
        live_photo_video_path=None,
        apple_score=0.75,
        sha256=None,
        dhash=None,
        phash=None,
        dhash_small=None,
        phash_small=None,
        motion_score=None,
    )
    defaults.update(overrides)
    return MediaItem(**defaults)


def make_group(
    items: list[MediaItem],
    match_type: MatchType = MatchType.EXACT,
    group_id: int = 1,
    recommended_keep_uuid: str | None = None,
) -> DuplicateGroup:
    """Build a DuplicateGroup from a list of items."""
    return DuplicateGroup(
        group_id=group_id,
        match_type=match_type,
        items=items,
        recommended_keep_uuid=recommended_keep_uuid,
    )


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """Config with db_path in a temporary directory."""
    return Config(db_path=tmp_path / "cache.db")


@pytest.fixture()
def cache(tmp_path: Path) -> CacheDB:
    """Fresh CacheDB in a temporary directory."""
    db = CacheDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture()
def populated_cache(cache: CacheDB) -> CacheDB:
    """Cache pre-loaded with a variety of items."""
    items = [
        sample_item(
            uuid="photo-1",
            filename="IMG_0001.heic",
            file_size=2_000_000,
            has_gps=True,
            persons=["Alice"],
            keywords=["beach"],
            date_created=datetime(2024, 1, 15),
        ),
        sample_item(
            uuid="photo-2",
            filename="IMG_0002.heic",
            file_size=2_000_000,
            has_gps=False,
            persons=[],
            keywords=[],
            date_created=datetime(2024, 3, 20),
        ),
        sample_item(
            uuid="photo-3",
            filename="IMG_0003.jpg",
            uti="public.jpeg",
            file_size=1_500_000,
            has_gps=True,
            persons=["Bob"],
            keywords=["sunset"],
            date_created=datetime(2024, 6, 10),
            albums=["Summer"],
        ),
        sample_item(
            uuid="photo-no-date",
            filename="IMG_0004.heic",
            file_size=1_000_000,
            date_created=None,
            date_modified=None,
            has_gps=False,
            persons=[],
            keywords=[],
        ),
        sample_item(
            uuid="video-1",
            filename="MOV_0001.mov",
            media_type=MediaType.VIDEO,
            uti="com.apple.quicktime-movie",
            file_size=50_000_000,
            width=1920,
            height=1080,
            duration=30.5,
            date_created=datetime(2024, 2, 10),
        ),
        sample_item(
            uuid="video-2",
            filename="MOV_0002.mov",
            media_type=MediaType.VIDEO,
            uti="com.apple.quicktime-movie",
            file_size=50_000_000,
            width=1920,
            height=1080,
            duration=31.0,
            date_created=datetime(2024, 2, 11),
        ),
        sample_item(
            uuid="screenshot-1",
            filename="Screenshot_001.png",
            uti="public.png",
            file_size=500_000,
            width=2880,
            height=1800,
            is_screenshot=True,
            has_gps=False,
            persons=[],
            keywords=[],
            date_created=datetime(2024, 4, 1),
        ),
        sample_item(
            uuid="live-1",
            filename="IMG_LIVE_001.heic",
            media_type=MediaType.LIVE_PHOTO,
            file_size=3_000_000,
            live_photo_uuid="live-video-1",
            date_created=datetime(2024, 5, 5),
        ),
    ]
    cache.upsert_items_batch(items)
    return cache
