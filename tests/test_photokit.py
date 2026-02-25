"""Tests for PhotoKit bridge (Swift CLI wrapper)."""

import json
from unittest.mock import MagicMock, patch
from pathlib import Path

import subprocess as sp

from media_scanner.actions.photokit import (
    _compile_swift_bridge,
    _needs_recompile,
    _swift_binary_path,
    create_deletion_album_photokit,
    update_metadata_photokit,
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

    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._SWIFT_SOURCE")
    @patch("media_scanner.actions.photokit._BINARY_PATH")
    def test_source_newer_needs_recompile(self, mock_binary, mock_source, mock_plist):
        mock_binary.exists.return_value = True
        mock_binary.stat.return_value = MagicMock(st_mtime=100)
        mock_source.stat.return_value = MagicMock(st_mtime=200)
        mock_plist.exists.return_value = False
        assert _needs_recompile() is True

    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._SWIFT_SOURCE")
    @patch("media_scanner.actions.photokit._BINARY_PATH")
    def test_binary_newer_no_recompile(self, mock_binary, mock_source, mock_plist):
        mock_binary.exists.return_value = True
        mock_binary.stat.return_value = MagicMock(st_mtime=200)
        mock_source.stat.return_value = MagicMock(st_mtime=100)
        mock_plist.exists.return_value = False
        assert _needs_recompile() is False


class TestCompileSwiftBridge:
    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._APP_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_calls_swiftc(self, mock_which, mock_run, mock_dir, mock_plist):
        mock_run.return_value = MagicMock(returncode=0)
        mock_plist.exists.return_value = False
        (mock_dir / "Contents" / "MacOS").mkdir = MagicMock()
        result = _compile_swift_bridge()

        assert result is True
        swiftc_call = mock_run.call_args_list[0]
        assert "swiftc" in swiftc_call[0][0][0]
        assert "-framework" in swiftc_call[0][0]
        assert "Photos" in swiftc_call[0][0]

    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._APP_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_signs_bundle(self, mock_which, mock_run, mock_dir, mock_plist):
        mock_run.return_value = MagicMock(returncode=0)
        mock_plist.exists.return_value = False
        (mock_dir / "Contents" / "MacOS").mkdir = MagicMock()
        _compile_swift_bridge()

        # swiftc + codesign binary + codesign bundle = 3 calls
        assert mock_run.call_count == 3
        codesign_calls = [c for c in mock_run.call_args_list if "codesign" in c[0][0][0]]
        assert len(codesign_calls) == 2

    @patch("media_scanner.actions.photokit.shutil.which", return_value=None)
    def test_compile_failure_no_swiftc(self, mock_which):
        assert _compile_swift_bridge() is False

    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._APP_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_failure_swiftc_error(self, mock_which, mock_run, mock_dir, mock_plist):
        mock_run.return_value = MagicMock(returncode=1)
        mock_plist.exists.return_value = False
        (mock_dir / "Contents" / "MacOS").mkdir = MagicMock()
        assert _compile_swift_bridge() is False


class TestCreateDeletionAlbumPhotokit:
    def test_empty_uuids_returns_success(self):
        result = create_deletion_album_photokit([], "Test Album")
        assert result["success"] is True

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_passes_uuids_and_album(self, mock_recompile, mock_bridge):
        mock_bridge.return_value = {"returncode": 0, "stdout": "3", "stderr": ""}
        uuids = ["uuid-1", "uuid-2", "uuid-3"]

        create_deletion_album_photokit(uuids, "My Album")

        args, stdin_data = mock_bridge.call_args[0]
        assert "--album" in args
        assert "My Album" in args
        assert stdin_data == "uuid-1\nuuid-2\nuuid-3"

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_success_returns_dict(self, mock_recompile, mock_bridge):
        mock_bridge.return_value = {"returncode": 0, "stdout": "5", "stderr": ""}
        result = create_deletion_album_photokit(["uuid-1"], "Album")
        assert result["success"] is True
        assert result["error"] is None

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_failure_returns_error(self, mock_recompile, mock_bridge):
        mock_bridge.return_value = {"returncode": 1, "stdout": "", "stderr": "something broke"}
        result = create_deletion_album_photokit(["uuid-1"], "Album")
        assert result["success"] is False
        assert result["error"] == "something broke"

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_auth_denied_error(self, mock_recompile, mock_bridge):
        mock_bridge.return_value = {
            "returncode": 2,
            "stdout": "",
            "stderr": "PhotoKit authorization denied (status=2).",
        }
        result = create_deletion_album_photokit(["uuid-1"], "Album")
        assert result["success"] is False
        assert result["error"] == "auth_denied"

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_timeout_returns_error(self, mock_recompile, mock_bridge):
        mock_bridge.side_effect = sp.TimeoutExpired(cmd="open", timeout=600)
        result = create_deletion_album_photokit(["uuid-1"], "Album")
        assert result["success"] is False
        assert result["error"] == "timeout"

    @patch("media_scanner.actions.photokit._compile_swift_bridge", return_value=False)
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=True)
    def test_fallback_when_compile_fails(self, mock_recompile, mock_compile):
        result = create_deletion_album_photokit(["uuid-1"], "Album")
        assert result["success"] is False
        assert result["error"] == "compile_failed"


class TestCompileIncludesFrameworks:
    @patch("media_scanner.actions.photokit._INFO_PLIST")
    @patch("media_scanner.actions.photokit._APP_DIR")
    @patch("media_scanner.actions.photokit.subprocess.run")
    @patch("media_scanner.actions.photokit.shutil.which", return_value="/usr/bin/swiftc")
    def test_compile_includes_all_frameworks(self, mock_which, mock_run, mock_dir, mock_plist):
        mock_run.return_value = MagicMock(returncode=0)
        mock_plist.exists.return_value = False
        (mock_dir / "Contents" / "MacOS").mkdir = MagicMock()
        _compile_swift_bridge()

        swiftc_call = mock_run.call_args_list[0]
        args = swiftc_call[0][0]
        framework_indices = [i for i, a in enumerate(args) if a == "-framework"]
        frameworks = [args[i + 1] for i in framework_indices]
        assert "Photos" in frameworks
        assert "CoreLocation" in frameworks
        assert "AppKit" in frameworks


class TestUpdateMetadataPhotokit:
    def test_empty_transfers_returns_success(self):
        result = update_metadata_photokit([])
        assert result["success"] is True
        assert result["success_count"] == 0
        assert result["error_count"] == 0

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_sends_json_payload(self, mock_recompile, mock_bridge):
        response = {"success_count": 1, "error_count": 0, "errors": []}
        mock_bridge.return_value = {
            "returncode": 0,
            "stdout": json.dumps(response),
            "stderr": "",
        }
        transfers = [{"uuid": "test-uuid", "date": "2020-01-01T00:00:00"}]

        update_metadata_photokit(transfers)

        args, stdin_data = mock_bridge.call_args[0]
        assert "--update-metadata" in args
        payload = json.loads(stdin_data)
        assert payload[0]["uuid"] == "test-uuid"

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_parses_success_response(self, mock_recompile, mock_bridge):
        response = {"success_count": 2, "error_count": 0, "errors": []}
        mock_bridge.return_value = {
            "returncode": 0,
            "stdout": json.dumps(response),
            "stderr": "",
        }
        transfers = [
            {"uuid": "uuid-1", "date": "2020-01-01T00:00:00"},
            {"uuid": "uuid-2", "latitude": 37.7, "longitude": -122.4},
        ]

        result = update_metadata_photokit(transfers)
        assert result["success"] is True
        assert result["success_count"] == 2
        assert result["error_count"] == 0

    @patch("media_scanner.actions.photokit._compile_swift_bridge", return_value=False)
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=True)
    def test_compile_failure(self, mock_recompile, mock_compile):
        result = update_metadata_photokit([{"uuid": "test"}])
        assert result["success"] is False
        assert result["error_count"] == 1
        assert "compilation failed" in result["errors"][0]

    @patch("media_scanner.actions.photokit._run_bridge")
    @patch("media_scanner.actions.photokit._needs_recompile", return_value=False)
    def test_handles_partial_failure(self, mock_recompile, mock_bridge):
        response = {
            "success_count": 1,
            "error_count": 1,
            "errors": ["uuid-2:Asset not found"],
        }
        mock_bridge.return_value = {
            "returncode": 0,
            "stdout": json.dumps(response),
            "stderr": "",
        }
        transfers = [
            {"uuid": "uuid-1", "date": "2020-01-01T00:00:00"},
            {"uuid": "uuid-2", "date": "2020-01-01T00:00:00"},
        ]

        result = update_metadata_photokit(transfers)
        assert result["success"] is False
        assert result["success_count"] == 1
        assert result["error_count"] == 1
