"""Tests for Photos.app AppleScript bridge."""

from unittest.mock import MagicMock, patch, call

from media_scanner.actions.applescript import (
    ALBUM_NAME,
    create_deletion_album,
)


class TestCreateDeletionAlbum:
    def test_empty_list_returns_true(self):
        assert create_deletion_album([]) is True

    @patch("media_scanner.actions.applescript._add_batch_to_album", return_value=True)
    @patch("media_scanner.actions.applescript._ensure_album_exists", return_value=True)
    def test_calls_ensure_album_then_batches(self, mock_ensure, mock_batch):
        result = create_deletion_album(["uuid-1", "uuid-2"])

        assert result is True
        mock_ensure.assert_called_once()
        mock_batch.assert_called_once_with(["uuid-1", "uuid-2"])

    @patch("media_scanner.actions.applescript._add_batch_to_album", return_value=True)
    @patch("media_scanner.actions.applescript._ensure_album_exists", return_value=False)
    def test_returns_false_if_album_creation_fails(self, mock_ensure, mock_batch):
        result = create_deletion_album(["uuid-1"])

        assert result is False
        mock_batch.assert_not_called()

    @patch("media_scanner.actions.applescript._add_batch_to_album")
    @patch("media_scanner.actions.applescript._ensure_album_exists", return_value=True)
    def test_processes_in_batches(self, mock_ensure, mock_batch):
        mock_batch.return_value = True
        uuids = [f"uuid-{i}" for i in range(250)]

        result = create_deletion_album(uuids, batch_size=100)

        assert result is True
        assert mock_batch.call_count == 3  # 100 + 100 + 50

    @patch("media_scanner.actions.applescript._add_batch_to_album")
    @patch("media_scanner.actions.applescript._ensure_album_exists", return_value=True)
    def test_stops_on_batch_failure(self, mock_ensure, mock_batch):
        mock_batch.side_effect = [True, False]
        uuids = [f"uuid-{i}" for i in range(200)]

        result = create_deletion_album(uuids, batch_size=100)

        assert result is False
        assert mock_batch.call_count == 2


class TestRunAppleScript:
    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_ensure_album_calls_osascript_with_file(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        from media_scanner.actions.applescript import _ensure_album_exists

        result = _ensure_album_exists()

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"
        # Should be a file path, not -e
        assert args[1].endswith(".scpt")

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_add_batch_calls_osascript_with_file(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        from media_scanner.actions.applescript import _add_batch_to_album

        result = _add_batch_to_album(["abc-123", "def-456"])

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"
        assert args[1].endswith(".scpt")

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_timeout_returns_false(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="osascript", timeout=600)
        from media_scanner.actions.applescript import _add_batch_to_album

        assert _add_batch_to_album(["uuid-1"]) is False

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_failure_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        from media_scanner.actions.applescript import _add_batch_to_album

        assert _add_batch_to_album(["uuid-1"]) is False
