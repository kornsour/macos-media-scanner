"""Photos.app bridge via PhotoKit — fast album creation and metadata updates.

The Swift binary is packaged as a .app bundle at ~/.media-scanner/PhotosBridge.app
so macOS treats it as a real application and displays the Photos permission prompt.
(Plain CLI tools are silently denied TCC access on macOS 14+.)

Launched via ``open --wait-apps`` with file-based I/O since ``open`` does not
forward stdin/stdout/stderr.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


_SWIFT_DIR = Path(__file__).parent / "swift"
_SWIFT_SOURCE = _SWIFT_DIR / "photos_bridge.swift"
_INFO_PLIST = _SWIFT_DIR / "Info.plist"
_APP_DIR = Path.home() / ".media-scanner" / "PhotosBridge.app"
_BINARY_PATH = _APP_DIR / "Contents" / "MacOS" / "photos-bridge"


def _swift_binary_path() -> Path:
    return _BINARY_PATH


def _needs_recompile() -> bool:
    if not _BINARY_PATH.exists():
        return True
    bin_mtime = _BINARY_PATH.stat().st_mtime
    if _SWIFT_SOURCE.stat().st_mtime > bin_mtime:
        return True
    if _INFO_PLIST.exists() and _INFO_PLIST.stat().st_mtime > bin_mtime:
        return True
    return False


def _compile_swift_bridge() -> bool:
    """Compile the Swift PhotoKit bridge as a .app bundle.

    Creates a minimal .app at ~/.media-scanner/PhotosBridge.app so macOS
    treats it as a real application and will display the Photos permission
    prompt.

    Returns False if swiftc is missing.
    """
    if not shutil.which("swiftc"):
        return False

    macos_dir = _APP_DIR / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    # Copy Info.plist into the .app bundle
    app_plist = _APP_DIR / "Contents" / "Info.plist"
    if _INFO_PLIST.exists():
        shutil.copy2(str(_INFO_PLIST), str(app_plist))

    try:
        result = subprocess.run(
            [
                "swiftc", "-o", str(_BINARY_PATH), str(_SWIFT_SOURCE),
                "-framework", "Photos", "-framework", "CoreLocation",
                "-framework", "AppKit",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False

        # Sign the binary first, then the bundle
        subprocess.run(
            ["codesign", "-s", "-", "--force", str(_BINARY_PATH)],
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["codesign", "-s", "-", "--force", str(_APP_DIR)],
            capture_output=True,
            timeout=30,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _run_bridge(args: list[str], stdin_data: str, timeout: int = 600) -> dict:
    """Launch the PhotosBridge .app via ``open --wait-apps``.

    Uses temp files for stdin/stdout/stderr since ``open`` does not pipe them.
    Returns {"returncode": int, "stdout": str, "stderr": str}.
    """
    tmp_dir = _APP_DIR.parent
    stdin_file = tmp_dir / "bridge-stdin.tmp"
    stdout_file = tmp_dir / "bridge-stdout.tmp"
    stderr_file = tmp_dir / "bridge-stderr.tmp"

    try:
        stdin_file.write_text(stdin_data)
        stdout_file.write_text("")
        stderr_file.write_text("")

        file_args = [
            "--stdin-file", str(stdin_file),
            "--stdout-file", str(stdout_file),
            "--stderr-file", str(stderr_file),
        ]

        result = subprocess.run(
            [
                "open", "--wait-apps", str(_APP_DIR),
                "--args", *args, *file_args,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout = stdout_file.read_text() if stdout_file.exists() else ""
        stderr_text = stderr_file.read_text() if stderr_file.exists() else ""

        # open always returns 0 if the app launched; check stderr for errors
        returncode = result.returncode
        if "authorization denied" in stderr_text.lower():
            returncode = 2
        elif stderr_text.strip() and not stdout.strip():
            returncode = 1

        return {"returncode": returncode, "stdout": stdout, "stderr": stderr_text}
    finally:
        stdin_file.unlink(missing_ok=True)
        stdout_file.unlink(missing_ok=True)
        stderr_file.unlink(missing_ok=True)


def create_deletion_album_photokit(uuids: list[str], album_name: str) -> dict:
    """Create an album in Photos.app using PhotoKit.

    Compiles the Swift bridge on first use (cached at ~/.media-scanner/).
    Returns {"success": bool, "error": str | None}.
    """
    if not uuids:
        return {"success": True, "error": None}

    if _needs_recompile():
        if not _compile_swift_bridge():
            return {"success": False, "error": "compile_failed"}

    try:
        result = _run_bridge(
            ["--album", album_name],
            "\n".join(uuids),
            timeout=600,
        )
        if result["returncode"] == 0 and result["stdout"].strip():
            return {"success": True, "error": None}
        stderr = result["stderr"].strip()
        if "authorization denied" in stderr.lower() or result["returncode"] == 2:
            return {"success": False, "error": "auth_denied"}
        return {"success": False, "error": stderr or f"exit code {result['returncode']}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except (FileNotFoundError, OSError) as e:
        return {"success": False, "error": str(e)}


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
        result = _run_bridge(
            ["--update-metadata"],
            payload,
            timeout=120,
        )
        if result["returncode"] != 0:
            return {
                "success": False,
                "success_count": 0,
                "error_count": len(transfers),
                "errors": [result["stderr"].strip() or "Unknown error"],
            }
        response = json.loads(result["stdout"])
        response["success"] = response.get("error_count", 0) == 0
        return response
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "success_count": 0,
            "error_count": len(transfers),
            "errors": ["Timeout"],
        }
    except (FileNotFoundError, OSError) as e:
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
