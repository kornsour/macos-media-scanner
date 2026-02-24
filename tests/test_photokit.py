"""Tests for PhotoKit bridge (Swift CLI wrapper)."""

from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import subprocess as sp

from media_scanner.actions.photokit import (
    _compile_swift_bridge,
    _needs_recompile,
    _swift_binary_path,
    create_deletion_album_photokit,
)


class TestSwiftBinaryPath:
    def test_returns_path_in_home(self):
        path = _swift_binary_path()
        assert path.name == "photos-bridge"
        assert ".media-scanner" in str(path)


class TestNeedsRecompile:
    @patch("media_scanner.actions.photokit._BINARY_PATH")
    def test_no_binary_needs_recompile(self, mock_path):
        mock_path.exists.return_value = False
        assert _needs_recompile() is True

    @patch("media_scanner.actions.photokit._SWIFT_SOURCE")
    @patch("media_scanner.actions.photokit._BINARY_PATH")
    def test_source_newer_needs_recompile(self, mock_binary, mock_source):
        mock_binary.exists.return_value = True
        mock_binary.stat.return_value = MagicMock(st_mtime=100)
        mock_source.stat.return_value = MagicMock(st_mtime=200)
        assert _needs_recompile() is True

    @patch("media_scanner.actions.photokit._SWIFT_SOURCE")
    @patch("media_scanner.actions.photokit._BINARY_PATH")
    def test_binary_newer_no_recompile(self, mock_binary, mock_source):
        mock_binary.exists.return_value = True
        mock_binary.stat.return_value = MagicMock(st_mtime=200)
        mock_source.stat.return_value = MagicMock(st_mtime=100)
        assert _needs_recompile() is False


class TestCompileSwiftBridge:
    @patch("media_scanner.actions.photokit._BINARY_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_calls_swiftc(self, mock_which, mock_run, mock_dir):
        mock_run.return_value = MagicMock(returncode=0)
        result = _compile_swift_bridge()

        assert result is True
        # First call should be swiftc
        swiftc_call = mock_run.call_args_list[0]
        assert "swiftc" in swiftc_call[0][0][0]
        assert "-framework" in swiftc_call[0][0]
        assert "Photos" in swiftc_call[0][0]

    @patch("media_scanner.actions.photokit._BINARY_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_signs_binary(self, mock_which, mock_run, mock_dir):
        mock_run.return_value = MagicMock(returncode=0)
        _compile_swift_bridge()

        # Second call should be codesign
        assert mock_run.call_count == 2
        codesign_call = mock_run.call_args_list[1]
        assert "codesign" in codesign_call[0][0][0]

    @patch("media_scanner.actions.photokit.shutil.which", return_value=None)
    def test_compile_failure_no_swiftc(self, mock_which):
        assert _compile_swift_bridge() is False

    @patch("media_scanner.actions.photokit._BINARY_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_failure_swiftc_error(self, mock_which, mock_run, mock_dir):
        mock_run.return_value = MagicMock(returncode=1)
        assert _compile_swift_bridge() is False


class TestCreateDeletionAlbumPhotokit:
    def test_empty_uuids_returns_true(self):
        assert create_deletion_album_photokit([], "Test Album") is True

    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_pipes_uuids_to_stdin(self, mock_recompile, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        uuids = ["uuid-1", "uuid-2", "uuid-3"]

        create_deletion_album_photokit(uuids, "My Album")

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["input"] == "uuid-1\nuuid-2\nuuid-3"

    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_passes_album_name(self, mock_recompile, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        create_deletion_album_photokit(["uuid-1"], "My Album")

        call_args = mock_run.call_args[0][0]
        assert "--album" in call_args
        assert "My Album" in call_args

    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_success_returns_true(self, mock_recompile, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert create_deletion_album_photokit(["uuid-1"], "Album") is True

    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_failure_returns_false(self, mock_recompile, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert create_deletion_album_photokit(["uuid-1"], "Album") is False

    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_timeout_returns_false(self, mock_recompile, mock_run):
        mock_run.side_effect = sp.TimeoutExpired(cmd="photos-bridge", timeout=120)
        assert create_deletion_album_photokit(["uuid-1"], "Album") is False

    @patch("media_scanner.actions.photokit._compile_swift_bridge", return_value=False)
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=True)
    def test_fallback_when_compile_fails(self, mock_recompile, mock_compile):
        assert create_deletion_album_photokit(["uuid-1"], "Album") is False
