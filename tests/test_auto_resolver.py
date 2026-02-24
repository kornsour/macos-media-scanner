"""Tests for auto_resolver module."""

from __future__ import annotations

from media_scanner.core.auto_resolver import auto_resolve
from media_scanner.data.models import ActionType, MatchType

from tests.conftest import make_group, sample_item


class TestAutoResolve:
    def test_keeps_highest_scored_item(self, config):
        """Best item (highest resolution, most metadata) gets KEEP."""
        best = sample_item(
            uuid="best",
            file_size=5_000_000,
            width=6000,
            height=4000,
            has_gps=True,
            persons=["Alice"],
            keywords=["beach"],
            albums=["Vacation"],
        )
        worse = sample_item(
            uuid="worse",
            file_size=1_000_000,
            width=1920,
            height=1080,
            has_gps=False,
            persons=[],
            keywords=[],
            albums=[],
        )
        group = make_group([worse, best], group_id=1)

        actions = auto_resolve([group], config)
        keep_uuids = [a.uuid for a in actions if a.action == ActionType.KEEP]
        assert keep_uuids == ["best"]

    def test_deletes_remaining_items(self, config):
        """All items except the best get DELETE."""
        items = [
            sample_item(uuid="a", file_size=3_000_000, width=4032, height=3024),
            sample_item(uuid="b", file_size=1_000_000, width=1920, height=1080),
            sample_item(uuid="c", file_size=500_000, width=1280, height=720),
        ]
        group = make_group(items, group_id=1)

        actions = auto_resolve([group], config)
        delete_uuids = sorted(a.uuid for a in actions if a.action == ActionType.DELETE)
        assert delete_uuids == ["b", "c"]

    def test_multiple_groups(self, config):
        """All groups are processed."""
        g1 = make_group(
            [
                sample_item(uuid="g1-a", file_size=2_000_000),
                sample_item(uuid="g1-b", file_size=1_000_000),
            ],
            group_id=1,
        )
        g2 = make_group(
            [
                sample_item(uuid="g2-a", file_size=3_000_000),
                sample_item(uuid="g2-b", file_size=1_500_000),
            ],
            group_id=2,
        )

        actions = auto_resolve([g1, g2], config)
        assert len(actions) == 4

        keeps = [a.uuid for a in actions if a.action == ActionType.KEEP]
        deletes = [a.uuid for a in actions if a.action == ActionType.DELETE]
        assert len(keeps) == 2
        assert len(deletes) == 2

    def test_empty_groups_list(self, config):
        """Empty input returns empty list."""
        assert auto_resolve([], config) == []

    def test_single_item_group(self, config):
        """A group with one item should get KEEP, nothing deleted."""
        group = make_group(
            [sample_item(uuid="solo")],
            group_id=1,
        )

        actions = auto_resolve([group], config)
        assert len(actions) == 1
        assert actions[0].uuid == "solo"
        assert actions[0].action == ActionType.KEEP

    def test_action_group_ids_set(self, config):
        """Each action has the correct group_id."""
        g1 = make_group(
            [
                sample_item(uuid="a", file_size=2_000_000),
                sample_item(uuid="b", file_size=1_000_000),
            ],
            group_id=10,
        )
        g2 = make_group(
            [
                sample_item(uuid="c", file_size=3_000_000),
                sample_item(uuid="d", file_size=1_000_000),
            ],
            group_id=20,
        )

        actions = auto_resolve([g1, g2], config)
        for action in actions:
            if action.uuid in ("a", "b"):
                assert action.group_id == 10
            else:
                assert action.group_id == 20
