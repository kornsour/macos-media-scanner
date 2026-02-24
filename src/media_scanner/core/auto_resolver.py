"""Automated duplicate resolution - accept quality scorer recommendations."""

from __future__ import annotations

from media_scanner.config import Config
from media_scanner.core.quality_scorer import rank_group
from media_scanner.data.models import ActionRecord, ActionType, DuplicateGroup


def auto_resolve(
    groups: list[DuplicateGroup], config: Config
) -> list[ActionRecord]:
    """Accept the quality scorer's recommendation for every group.

    For each group: rank items, keep the top-scored item, mark the rest for deletion.
    Returns a flat list of ActionRecords.
    """
    actions: list[ActionRecord] = []
    for group in groups:
        rank_group(group, config)
        for item in group.items:
            if item.uuid == group.recommended_keep_uuid:
                actions.append(
                    ActionRecord(
                        uuid=item.uuid,
                        action=ActionType.KEEP,
                        group_id=group.group_id,
                    )
                )
            else:
                actions.append(
                    ActionRecord(
                        uuid=item.uuid,
                        action=ActionType.DELETE,
                        group_id=group.group_id,
                    )
                )
    return actions
