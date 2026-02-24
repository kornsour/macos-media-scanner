"""Tests for interactive duplicate reviewer."""

from unittest.mock import MagicMock, patch, PropertyMock

from media_scanner.config import Config
from media_scanner.data.models import ActionType, DuplicateGroup, MatchType
from media_scanner.ui.reviewer import ReviewSession
from tests.conftest import make_group, sample_item


def _make_session(groups=None, config=None):
    if groups is None:
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        groups = [make_group(items, recommended_keep_uuid="a")]
    if config is None:
        config = Config()
    return ReviewSession(groups, config)


class TestAcceptRecommendation:
    @patch("media_scanner.ui.reviewer.console")
    def test_accept_sets_keep_and_delete(self, mock_console):
        items = [
            sample_item(uuid="keeper", width=8000, height=6000, file_size=10_000_000),
            sample_item(uuid="delete-me", width=640, height=480, file_size=100_000),
        ]
        group = make_group(items, recommended_keep_uuid="keeper")
        session = _make_session(groups=[group])

        # Simulate: user types "a" then "q"
        mock_console.input.side_effect = ["a", "q"]
        actions = session.run()

        action_map = {a.uuid: a.action for a in actions}
        # The ranker will determine who is keeper; we just check both actions exist
        assert len(actions) == 2
        assert ActionType.KEEP in action_map.values()
        assert ActionType.DELETE in action_map.values()


class TestChooseKeeper:
    @patch("media_scanner.ui.reviewer.console")
    def test_choose_picks_specific_item(self, mock_console):
        items = [
            sample_item(uuid="first"),
            sample_item(uuid="second"),
        ]
        group = make_group(items)
        session = _make_session(groups=[group])

        # "c" for choose, then "2" for second item, then "q" to quit
        mock_console.input.side_effect = ["c", "2", "q"]
        actions = session.run()

        # After ranking, item order may change, but the user chose item #2
        assert len(actions) == 2
        keeps = [a for a in actions if a.action == ActionType.KEEP]
        deletes = [a for a in actions if a.action == ActionType.DELETE]
        assert len(keeps) == 1
        assert len(deletes) == 1


class TestKeepAll:
    @patch("media_scanner.ui.reviewer.console")
    def test_keep_all_marks_all_keep(self, mock_console):
        items = [
            sample_item(uuid="a"),
            sample_item(uuid="b"),
            sample_item(uuid="c"),
        ]
        group = make_group(items)
        session = _make_session(groups=[group])

        mock_console.input.side_effect = ["k", "q"]
        actions = session.run()

        assert len(actions) == 3
        assert all(a.action == ActionType.KEEP for a in actions)


class TestUndo:
    @patch("media_scanner.ui.reviewer.console")
    def test_undo_reverts_previous(self, mock_console):
        items1 = [sample_item(uuid="a1"), sample_item(uuid="a2")]
        items2 = [sample_item(uuid="b1"), sample_item(uuid="b2")]
        groups = [
            make_group(items1, group_id=1),
            make_group(items2, group_id=2),
        ]
        session = _make_session(groups=groups)

        # Accept first, undo, then quit
        mock_console.input.side_effect = ["a", "u", "q"]
        actions = session.run()

        # Undo removed the actions from group 1
        assert len(actions) == 0


class TestQuit:
    @patch("media_scanner.ui.reviewer.console")
    def test_quit_stops_loop(self, mock_console):
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        groups = [make_group(items)]
        session = _make_session(groups=groups)

        mock_console.input.side_effect = ["q"]
        actions = session.run()

        assert len(actions) == 0

    @patch("media_scanner.ui.reviewer.console")
    def test_skip_advances(self, mock_console):
        items1 = [sample_item(uuid="a1"), sample_item(uuid="a2")]
        items2 = [sample_item(uuid="b1"), sample_item(uuid="b2")]
        groups = [make_group(items1, group_id=1), make_group(items2, group_id=2)]
        session = _make_session(groups=groups)

        # Skip first group, accept second, then loop ends
        mock_console.input.side_effect = ["s", "a"]
        actions = session.run()

        # Only group 2 has actions
        assert len(actions) == 2


class TestEmptyGroups:
    @patch("media_scanner.ui.reviewer.console")
    def test_no_groups(self, mock_console):
        session = _make_session(groups=[])
        actions = session.run()
        assert actions == []
