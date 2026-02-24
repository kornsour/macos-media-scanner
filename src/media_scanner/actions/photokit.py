"""Photos.app bridge via PhotoKit — fast album creation and metadata updates."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


_SWIFT_SOURCE = Path(__file__).parent / "swift" / "photos_bridge.swift"
_BINARY_DIR = Path.home() / ".media-scanner" / "bin"
_BINARY_PATH = _BINARY_DIR / "photos-bridge"


def _swift_binary_path() -> Path:
    return _BINARY_PATH


def _needs_recompile() -> bool:
    if not _BINARY_PATH.exists():
        return True
    return _SWIFT_SOURCE.stat().st_mtime > _BINARY_PATH.stat().st_mtime


def _compile_swift_bridge() -> bool:
    """Compile the Swift PhotoKit bridge binary. Returns False if swiftc is missing."""
    if not shutil.which("swiftc"):
        return False

    _BINARY_DIR.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "swiftc", "-o", str(_BINARY_PATH), str(_SWIFT_SOURCE),
                "-framework", "Photos", "-framework", "CoreLocation",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False

        # Ad-hoc code sign so macOS allows execution
        subprocess.run(
            ["codesign", "-s", "-", str(_BINARY_PATH)],
            capture_output=True,
            timeout=30,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def create_deletion_album_photokit(uuids: list[str], album_name: str) -> bool:
    """Create an album in Photos.app using PhotoKit.

    Compiles the Swift bridge on first use (cached at ~/.media-scanner/bin/).
    Returns True on success, False if compilation or execution fails.
    """
    if not uuids:
        return True

    if _needs_recompile():
        if not _compile_swift_bridge():
            return False

    try:
        result = subprocess.run(
            [str(_BINARY_PATH), "--album", album_name],
            input="\n".join(uuids),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def update_metadata_photokit(transfers: list[dict]) -> dict:
    """Update metadata on Photos assets via PhotoKit.

    Each dict in transfers should have: uuid, and optionally date (ISO8601),
    latitude, longitude.

    Returns {"success": bool, "success_count": int, "error_count": int, "errors": [...]}.
    """
    if not transfers:
        return {"success": True, "success_count": 0, "error_count": 0, "errors": []}

    if _needs_recompile():
        if not _compile_swift_bridge():
            return {
                "success": False,
                "success_count": 0,
                "error_count": len(transfers),
                "errors": ["Swift bridge compilation failed"],
            }

    try:
        payload = json.dumps(transfers)
        result = subprocess.run(
            [str(_BINARY_PATH), "--update-metadata"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "success_count": 0,
                "error_count": len(transfers),
                "errors": [result.stderr.strip() or "Unknown error"],
            }
        response = json.loads(result.stdout)
        response["success"] = response.get("error_count", 0) == 0
        return response
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {
            "success": False,
            "success_count": 0,
            "error_count": len(transfers),
            "errors": [str(e)],
        }
    except json.JSONDecodeError:
        return {
            "success": False,
            "success_count": 0,
            "error_count": len(transfers),
            "errors": ["Invalid JSON response from Swift bridge"],
        }
