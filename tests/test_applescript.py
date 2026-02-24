"""Tests for Photos.app AppleScript bridge."""

from unittest.mock import MagicMock, patch

from media_scanner.actions.applescript import (
    ALBUM_NAME,
    create_album_batch,
    create_deletion_album,
)


class TestCreateDeletionAlbum:
    def test_empty_list_returns_true(self):
        assert create_deletion_album([]) is True

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_calls_osascript(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = create_deletion_album(["uuid-1", "uuid-2"])

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_script_contains_uuids(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        create_deletion_album(["abc-123", "def-456"])

        call_args = mock_run.call_args[0][0]
        script = call_args[2]  # -e argument
        assert "abc-123" in script
        assert "def-456" in script

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_script_contains_album_name(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        create_deletion_album(["uuid-1"])

        call_args = mock_run.call_args[0][0]
        script = call_args[2]
        assert ALBUM_NAME in script

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_failure_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert create_deletion_album(["uuid-1"]) is False

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_timeout_returns_false(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="osascript", timeout=300)
        assert create_deletion_album(["uuid-1"]) is False

    @patch("media_scanner.actions.applescript.subprocess.run")
    def test_file_not_found_returns_false(self, mock_run):
        mock_run.side_effect = FileNotFoundError("osascript not found")
        assert create_deletion_album(["uuid-1"]) is False


class TestCreateAlbumBatch:
    @patch("media_scanner.actions.applescript.create_deletion_album")
    def test_processes_in_batches(self, mock_create):
        mock_create.return_value = True
        uuids = [f"uuid-{i}" for i in range(250)]

        result = create_album_batch(uuids, batch_size=100)

        assert result is True
        assert mock_create.call_count == 3  # 100 + 100 + 50

    @patch("media_scanner.actions.applescript.create_deletion_album")
    def test_stops_on_failure(self, mock_create):
        mock_create.side_effect = [True, False]
        uuids = [f"uuid-{i}" for i in range(200)]

        result = create_album_batch(uuids, batch_size=100)

        assert result is False
        assert mock_create.call_count == 2

    @patch("media_scanner.actions.applescript.create_deletion_album")
    def test_single_batch(self, mock_create):
        mock_create.return_value = True
        uuids = ["uuid-1", "uuid-2"]

        result = create_album_batch(uuids, batch_size=100)

        assert result is True
        assert mock_create.call_count == 1
