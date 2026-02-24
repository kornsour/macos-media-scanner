"""Action queue management - wrapper around cache DB actions."""

from __future__ import annotations

from media_scanner.data.cache import CacheDB
from media_scanner.data.models import ActionRecord, ActionType


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
