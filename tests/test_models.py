"""Tests for data models: enums, dataclass defaults, field types."""

from datetime import datetime
from pathlib import Path

from media_scanner.data.models import (
    ActionRecord,
    ActionType,
    DuplicateGroup,
    MatchType,
    MediaItem,
    MediaType,
)


class TestMediaType:
    def test_values(self):
        assert MediaType.PHOTO.value == "photo"
        assert MediaType.VIDEO.value == "video"
        assert MediaType.LIVE_PHOTO.value == "live_photo"
        assert MediaType.OTHER.value == "other"

    def test_from_value(self):
        assert MediaType("photo") is MediaType.PHOTO
        assert MediaType("video") is MediaType.VIDEO

    def test_member_count(self):
        assert len(MediaType) == 4


class TestMatchType:
    def test_values(self):
        assert MatchType.EXACT.value == "exact"
        assert MatchType.NEAR.value == "near"
        assert MatchType.SIMILAR.value == "similar"

    def test_member_count(self):
        assert len(MatchType) == 3


class TestActionType:
    def test_values(self):
        assert ActionType.DELETE.value == "delete"
        assert ActionType.KEEP.value == "keep"
        assert ActionType.EXPORT.value == "export"
        assert ActionType.SKIP.value == "skip"

    def test_member_count(self):
        assert len(ActionType) == 4


class TestMediaItem:
    def test_required_fields(self):
        item = MediaItem(
            uuid="abc",
            filename="test.jpg",
            original_filename="test.jpg",
            path=Path("/tmp/test.jpg"),
            media_type=MediaType.PHOTO,
            file_size=100,
            width=640,
            height=480,
            date_created=None,
            date_modified=None,
            duration=None,
            uti="public.jpeg",
            has_gps=False,
            latitude=None,
            longitude=None,
        )
        assert item.uuid == "abc"
        assert item.file_size == 100

    def test_default_lists(self):
        item = MediaItem(
            uuid="abc",
            filename="test.jpg",
            original_filename="test.jpg",
            path=None,
            media_type=MediaType.PHOTO,
            file_size=0,
            width=0,
            height=0,
            date_created=None,
            date_modified=None,
            duration=None,
            uti="",
            has_gps=False,
            latitude=None,
            longitude=None,
        )
        assert item.albums == []
        assert item.persons == []
        assert item.keywords == []

    def test_default_booleans(self):
        item = MediaItem(
            uuid="abc",
            filename="test.jpg",
            original_filename="test.jpg",
            path=None,
            media_type=MediaType.PHOTO,
            file_size=0,
            width=0,
            height=0,
            date_created=None,
            date_modified=None,
            duration=None,
            uti="",
            has_gps=False,
            latitude=None,
            longitude=None,
        )
        assert item.is_edited is False
        assert item.is_favorite is False
        assert item.is_hidden is False
        assert item.is_screenshot is False
        assert item.is_selfie is False
        assert item.is_burst is False

    def test_default_hashes_are_none(self):
        item = MediaItem(
            uuid="abc",
            filename="test.jpg",
            original_filename="test.jpg",
            path=None,
            media_type=MediaType.PHOTO,
            file_size=0,
            width=0,
            height=0,
            date_created=None,
            date_modified=None,
            duration=None,
            uti="",
            has_gps=False,
            latitude=None,
            longitude=None,
        )
        assert item.sha256 is None
        assert item.dhash is None
        assert item.phash is None

    def test_path_can_be_none(self):
        """Cloud-only items have no local path."""
        item = MediaItem(
            uuid="cloud",
            filename="cloud.jpg",
            original_filename="cloud.jpg",
            path=None,
            media_type=MediaType.PHOTO,
            file_size=0,
            width=0,
            height=0,
            date_created=None,
            date_modified=None,
            duration=None,
            uti="",
            has_gps=False,
            latitude=None,
            longitude=None,
        )
        assert item.path is None


class TestDuplicateGroup:
    def test_defaults(self):
        group = DuplicateGroup(group_id=1, match_type=MatchType.EXACT)
        assert group.items == []
        assert group.recommended_keep_uuid is None

    def test_with_items(self):
        from tests.conftest import sample_item

        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        group = DuplicateGroup(
            group_id=1,
            match_type=MatchType.NEAR,
            items=items,
            recommended_keep_uuid="a",
        )
        assert len(group.items) == 2
        assert group.recommended_keep_uuid == "a"


class TestActionRecord:
    def test_defaults(self):
        action = ActionRecord(uuid="uuid-1", action=ActionType.DELETE)
        assert action.group_id is None
        assert action.applied is False
        assert action.applied_at is None
        assert isinstance(action.created_at, datetime)

    def test_explicit_fields(self):
        now = datetime(2024, 1, 1)
        action = ActionRecord(
            uuid="uuid-1",
            action=ActionType.KEEP,
            group_id=42,
            created_at=now,
            applied=True,
            applied_at=now,
        )
        assert action.group_id == 42
        assert action.applied is True
