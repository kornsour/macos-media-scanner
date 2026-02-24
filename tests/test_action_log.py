"""Tests for action log utilities."""

from media_scanner.actions.action_log import (
    get_action_summary,
    get_delete_uuids,
    undo_group_actions,
)
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionRecord, ActionType


class TestGetActionSummary:
    def test_empty(self, cache: CacheDB):
        assert get_action_summary(cache) == {}

    def test_counts_by_type(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE, group_id=1))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.DELETE, group_id=1))
        cache.save_action(ActionRecord(uuid="u3", action=ActionType.KEEP, group_id=1))

        summary = get_action_summary(cache)
        assert summary["delete"] == 2
        assert summary["keep"] == 1

    def test_ignores_applied(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE))
        cache.mark_actions_applied(["u1"])

        summary = get_action_summary(cache)
        assert summary == {}


class TestGetDeleteUuids:
    def test_returns_delete_uuids(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.KEEP))
        cache.save_action(ActionRecord(uuid="u3", action=ActionType.DELETE))

        uuids = get_delete_uuids(cache)
        assert set(uuids) == {"u1", "u3"}

    def test_empty(self, cache: CacheDB):
        assert get_delete_uuids(cache) == []


class TestUndoGroupActions:
    def test_removes_group_actions(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE, group_id=1))
        cache.save_action(ActionRecord(uuid="u2", action=ActionType.KEEP, group_id=1))
        cache.save_action(ActionRecord(uuid="u3", action=ActionType.DELETE, group_id=2))

        removed = undo_group_actions(cache, group_id=1)

        assert removed == 2
        remaining = cache.get_pending_actions()
        assert len(remaining) == 1
        assert remaining[0].uuid == "u3"

    def test_no_matching_group(self, cache: CacheDB):
        cache.save_action(ActionRecord(uuid="u1", action=ActionType.DELETE, group_id=1))

        removed = undo_group_actions(cache, group_id=99)

        assert removed == 0
        assert len(cache.get_pending_actions()) == 1

    def test_empty_cache(self, cache: CacheDB):
        removed = undo_group_actions(cache, group_id=1)
        assert removed == 0
