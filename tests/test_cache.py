"""Tests for CacheDB - SQLite operations."""

from datetime import datetime

import pytest

from media_scanner.data.cache import CacheDB
from media_scanner.data.models import (
    ActionRecord,
    ActionType,
    DuplicateGroup,
    MatchType,
    MediaType,
)
from tests.conftest import make_group, sample_item


class TestMediaItemCRUD:
    def test_upsert_and_get(self, cache: CacheDB):
        item = sample_item(uuid="test-1")
        cache.upsert_item(item)
        cache.conn.commit()

        retrieved = cache.get_item("test-1")
        assert retrieved is not None
        assert retrieved.uuid == "test-1"
        assert retrieved.filename == "IMG_0001.heic"
        assert retrieved.media_type == MediaType.PHOTO

    def test_get_missing_returns_none(self, cache: CacheDB):
        assert cache.get_item("nonexistent") is None

    def test_upsert_overwrites(self, cache: CacheDB):
        cache.upsert_item(sample_item(uuid="u1", filename="old.jpg"))
        cache.conn.commit()
        cache.upsert_item(sample_item(uuid="u1", filename="new.jpg"))
        cache.conn.commit()

        item = cache.get_item("u1")
        assert item.filename == "new.jpg"

    def test_batch_upsert(self, cache: CacheDB):
        items = [sample_item(uuid=f"batch-{i}") for i in range(5)]
        cache.upsert_items_batch(items)
        assert cache.item_count() == 5

    def test_get_all_items(self, cache: CacheDB):
        cache.upsert_items_batch([
            sample_item(uuid="a"),
            sample_item(uuid="b"),
        ])
        all_items = cache.get_all_items()
        assert len(all_items) == 2

    def test_get_items_by_type(self, populated_cache: CacheDB):
        videos = populated_cache.get_items_by_type(MediaType.VIDEO)
        assert len(videos) == 2
        assert all(v.media_type == MediaType.VIDEO for v in videos)

    def test_roundtrip_preserves_lists(self, cache: CacheDB):
        item = sample_item(
            uuid="lists",
            albums=["A", "B"],
            persons=["Alice", "Bob"],
            keywords=["k1", "k2"],
        )
        cache.upsert_item(item)
        cache.conn.commit()

        retrieved = cache.get_item("lists")
        assert retrieved.albums == ["A", "B"]
        assert retrieved.persons == ["Alice", "Bob"]
        assert retrieved.keywords == ["k1", "k2"]

    def test_roundtrip_preserves_booleans(self, cache: CacheDB):
        item = sample_item(
            uuid="bools",
            is_edited=True,
            is_favorite=True,
            is_hidden=True,
            is_screenshot=True,
        )
        cache.upsert_item(item)
        cache.conn.commit()

        retrieved = cache.get_item("bools")
        assert retrieved.is_edited is True
        assert retrieved.is_favorite is True
        assert retrieved.is_hidden is True
        assert retrieved.is_screenshot is True

    def test_roundtrip_preserves_dates(self, cache: CacheDB):
        dt = datetime(2024, 6, 15, 10, 30, 0)
        item = sample_item(uuid="dates", date_created=dt, date_modified=dt)
        cache.upsert_item(item)
        cache.conn.commit()

        retrieved = cache.get_item("dates")
        assert retrieved.date_created == dt
        assert retrieved.date_modified == dt

    def test_none_path_roundtrip(self, cache: CacheDB):
        item = sample_item(uuid="cloud", path=None)
        cache.upsert_item(item)
        cache.conn.commit()

        retrieved = cache.get_item("cloud")
        assert retrieved.path is None


class TestSizeGroups:
    def test_groups_items_with_same_size(self, cache: CacheDB):
        cache.upsert_items_batch([
            sample_item(uuid="a", file_size=1000),
            sample_item(uuid="b", file_size=1000),
            sample_item(uuid="c", file_size=2000),
        ])
        groups = cache.get_size_groups()
        assert 1000 in groups
        assert sorted(groups[1000]) == ["a", "b"]
        assert 2000 not in groups  # singleton

    def test_min_group_size(self, cache: CacheDB):
        cache.upsert_items_batch([
            sample_item(uuid="a", file_size=1000),
            sample_item(uuid="b", file_size=1000),
            sample_item(uuid="c", file_size=1000),
        ])
        groups_2 = cache.get_size_groups(min_group_size=2)
        assert 1000 in groups_2
        groups_4 = cache.get_size_groups(min_group_size=4)
        assert 1000 not in groups_4

    def test_empty_db(self, cache: CacheDB):
        assert cache.get_size_groups() == {}


class TestDurationGroups:
    def test_groups_by_similar_duration(self, populated_cache: CacheDB):
        groups = populated_cache.get_duration_groups(tolerance=2.0)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_videos(self, cache: CacheDB):
        cache.upsert_items_batch([sample_item(uuid="photo")])
        assert cache.get_duration_groups() == []

    def test_videos_outside_tolerance(self, cache: CacheDB):
        cache.upsert_items_batch([
            sample_item(uuid="v1", media_type=MediaType.VIDEO, duration=10.0),
            sample_item(uuid="v2", media_type=MediaType.VIDEO, duration=100.0),
        ])
        groups = cache.get_duration_groups(tolerance=2.0)
        assert len(groups) == 0


class TestHashUpdates:
    def test_update_hash(self, cache: CacheDB):
        cache.upsert_item(sample_item(uuid="h1"))
        cache.conn.commit()

        cache.update_hash("h1", "sha256", "abc123")
        item = cache.get_item("h1")
        assert item.sha256 == "abc123"

    def test_update_hashes_batch(self, cache: CacheDB):
        cache.upsert_items_batch([
            sample_item(uuid="h1"),
            sample_item(uuid="h2"),
        ])
        cache.update_hashes_batch([
            ("h1", "sha256", "hash1"),
            ("h2", "dhash", "hash2"),
        ])
        assert cache.get_item("h1").sha256 == "hash1"
        assert cache.get_item("h2").dhash == "hash2"

    def test_invalid_hash_type_raises(self, cache: CacheDB):
        cache.upsert_item(sample_item(uuid="h1"))
        cache.conn.commit()
        with pytest.raises(AssertionError):
            cache.update_hash("h1", "invalid", "value")


class TestDuplicateGroups:
    def test_save_and_retrieve(self, cache: CacheDB):
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        cache.upsert_items_batch(items)

        group = make_group(items, match_type=MatchType.EXACT)
        gid = cache.save_duplicate_group(group)
        assert gid > 0

        groups = cache.get_duplicate_groups()
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.EXACT
        assert len(groups[0].items) == 2

    def test_filter_by_match_type(self, cache: CacheDB):
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        cache.upsert_items_batch(items)

        cache.save_duplicate_group(make_group(items, match_type=MatchType.EXACT))
        cache.save_duplicate_group(make_group(items, match_type=MatchType.NEAR))

        exact = cache.get_duplicate_groups(match_type=MatchType.EXACT)
        near = cache.get_duplicate_groups(match_type=MatchType.NEAR)
        assert len(exact) == 1
        assert len(near) == 1

    def test_clear_duplicate_groups(self, cache: CacheDB):
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        cache.upsert_items_batch(items)
        cache.save_duplicate_group(make_group(items))

        cache.clear_duplicate_groups()
        assert cache.get_duplicate_groups() == []

    def test_recommended_keep_uuid(self, cache: CacheDB):
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        cache.upsert_items_batch(items)

        group = make_group(items, recommended_keep_uuid="a")
        cache.save_duplicate_group(group)

        retrieved = cache.get_duplicate_groups()[0]
        assert retrieved.recommended_keep_uuid == "a"


class TestActions:
    def test_save_and_get_pending(self, cache: CacheDB):
        action = ActionRecord(uuid="u1", action=ActionType.DELETE, group_id=1)
        cache.save_action(action)

        pending = cache.get_pending_actions()
        assert len(pending) == 1
        assert pending[0].uuid == "u1"
        assert pending[0].action == ActionType.DELETE

    def test_filter_by_action_type(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.KEEP))

        deletes = cache.get_pending_actions(action_type=ActionType.DELETE)
        assert len(deletes) == 1
        assert deletes[0].uuid == "u1"

    def test_mark_actions_applied(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.DELETE))

        cache.mark_actions_applied(["u1"])

        pending = cache.get_pending_actions()
        assert len(pending) == 1
        assert pending[0].uuid == "u2"

    def test_clear_pending_actions(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.KEEP))

        cache.clear_pending_actions()
        assert cache.get_pending_actions() == []


class TestScanMetadata:
    def test_set_and_get(self, cache: CacheDB):
        cache.set_scan_meta("last_scan", "2024-01-01")
        assert cache.get_scan_meta("last_scan") == "2024-01-01"

    def test_missing_key_returns_none(self, cache: CacheDB):
        assert cache.get_scan_meta("nonexistent") is None

    def test_upsert_semantics(self, cache: CacheDB):
        cache.set_scan_meta("key", "old")
        cache.set_scan_meta("key", "new")
        assert cache.get_scan_meta("key") == "new"


class TestStats:
    def test_empty_db(self, cache: CacheDB):
        stats = cache.get_stats()
        assert stats["total"] == 0
        assert stats["total_size"] == 0

    def test_populated(self, populated_cache: CacheDB):
        stats = populated_cache.get_stats()
        assert stats["total"] == 8
        assert stats["photos"] == 5  # includes screenshot (MediaType.PHOTO)
        assert stats["videos"] == 2
        assert stats["live_photos"] == 1
        assert stats["screenshots"] == 1
        assert stats["total_size"] > 0
        assert stats["no_date"] == 1
        assert isinstance(stats["type_distribution"], dict)

    def test_item_count(self, populated_cache: CacheDB):
        assert populated_cache.item_count() == 8
