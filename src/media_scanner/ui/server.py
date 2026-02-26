"""Local HTTP server for interactive duplicate review."""

from __future__ import annotations

import io
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PIL import Image

from media_scanner.actions.applescript import ALBUM_NAME, KEEPER_ALBUM_NAME
from media_scanner.core.metadata_merger import compute_transfers
from media_scanner.data.models import ActionRecord, ActionType

if TYPE_CHECKING:
    from media_scanner.config import Config
    from media_scanner.data.cache import CacheDB
    from media_scanner.data.models import DuplicateGroup, MediaItem

logger = logging.getLogger(__name__)

THUMB_SIZE = 240
THUMB_QUALITY = 65


def _make_thumbnail(item: MediaItem) -> bytes | None:
    """Generate JPEG thumbnail bytes for an item."""
    if not item.path or not item.path.exists():
        return None
    try:
        with Image.open(item.path) as img:
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY)
            return buf.getvalue()
    except Exception:
        return None


def _apply_photokit(
    delete_uuids: list[str], keep_uuid: str, transfers: list[dict]
) -> dict:
    """Apply metadata transfers and add deletes/keeper to albums via PhotoKit.

    Returns {"ok": bool, "error": str | None}.
    """
    from media_scanner.actions.photokit import (
        create_deletion_album_photokit,
        update_metadata_photokit,
    )

    # 1. Apply metadata transfers (date/GPS to keeper)
    if transfers:
        meta_result = update_metadata_photokit(transfers)
        if meta_result.get("error_count", 0) > 0:
            logger.warning(
                "Metadata transfer errors: %s", meta_result.get("errors", [])
            )

    # 2. Add duplicates to the deletion album
    pk_result = create_deletion_album_photokit(delete_uuids, ALBUM_NAME)
    if pk_result["success"]:
        # 3. Add keeper to the keepers album
        keeper_result = create_deletion_album_photokit([keep_uuid], KEEPER_ALBUM_NAME)
        if not keeper_result["success"]:
            logger.warning("Failed to add keeper to album: %s", keeper_result.get("error"))
        return {"ok": True, "error": None}

    # PhotoKit failed — try AppleScript fallback
    if pk_result["error"] == "auth_denied":
        logger.warning("PhotoKit auth denied, trying AppleScript fallback")
        from media_scanner.actions.applescript import create_deletion_album

        success = create_deletion_album(delete_uuids)
        if success:
            create_deletion_album([keep_uuid], album_name_override=KEEPER_ALBUM_NAME)
            return {"ok": True, "error": None}
        return {"ok": False, "error": "auth_denied"}

    return {"ok": False, "error": pk_result.get("error", "unknown")}


class ReviewHandler(BaseHTTPRequestHandler):
    """HTTP handler for the interactive review server."""

    cache: CacheDB
    config: Config
    groups: list[DuplicateGroup]
    items_by_uuid: dict[str, MediaItem]
    html_content: str
    thumb_cache: dict[str, bytes | None]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug(format, *args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/":
            body = self.html_content.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith("/thumb/"):
            uuid = path[7:]
            self._serve_thumbnail(uuid)

        elif path == "/api/summary":
            self._send_summary()

        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/merge":
            self._handle_merge(body)
        elif path == "/api/undo":
            self._handle_undo(body)
        else:
            self.send_error(404)

    def _serve_thumbnail(self, uuid: str) -> None:
        if uuid in self.thumb_cache:
            data = self.thumb_cache[uuid]
        else:
            item = self.items_by_uuid.get(uuid)
            data = _make_thumbnail(item) if item else None
            self.thumb_cache[uuid] = data

        if data:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def _handle_merge(self, body: dict) -> None:
        group_id = body.get("group_id")
        keep_uuid = body.get("keep_uuid")

        if group_id is None or not keep_uuid:
            self._send_json({"error": "Missing group_id or keep_uuid"}, 400)
            return

        # Find the group
        group = None
        for g in self.groups:
            if g.group_id == group_id:
                group = g
                break

        if not group:
            self._send_json({"error": "Group not found"}, 404)
            return

        # Build actions
        actions = []
        delete_uuids = []
        for item in group.items:
            is_keep = item.uuid == keep_uuid
            action = ActionRecord(
                uuid=item.uuid,
                action=ActionType.KEEP if is_keep else ActionType.DELETE,
                group_id=group_id,
            )
            actions.append(action)
            if not is_keep:
                delete_uuids.append(item.uuid)

        # Compute metadata transfers
        transfers = compute_transfers(actions, self.items_by_uuid)
        transfer_payload = []
        for t in transfers:
            entry: dict = {"uuid": t.keeper_uuid}
            if t.transfer_date:
                entry["date"] = t.transfer_date.isoformat()
            if t.transfer_latitude is not None and t.transfer_longitude is not None:
                entry["latitude"] = t.transfer_latitude
                entry["longitude"] = t.transfer_longitude
            transfer_payload.append(entry)

        # Apply via PhotoKit — add duplicates to album, keeper to keepers album
        pk_result = _apply_photokit(delete_uuids, keep_uuid, transfer_payload)

        if not pk_result["ok"]:
            error = pk_result.get("error", "unknown")
            if error == "auth_denied":
                self._send_json({
                    "ok": False,
                    "error": "Photos access denied. Open System Settings → "
                             "Privacy & Security → Photos and enable PhotosBridge.",
                }, 403)
            else:
                self._send_json({"ok": False, "error": error}, 500)
            return

        # Success — save actions as applied, clean up cache
        self.cache.clear_actions_for_group(group_id)
        for action in actions:
            action.applied = True
            self.cache.save_action(action)
        for t in transfers:
            self.cache.save_metadata_transfer(t)

        # Remove the group from the duplicate_groups table
        self.cache.delete_duplicate_group(group_id)

        # Remove from our in-memory list
        self.groups = [g for g in self.groups if g.group_id != group_id]

        self._send_json({
            "ok": True,
            "keep_uuid": keep_uuid,
            "deletes": len(delete_uuids),
            "transfers": len(transfers),
        })

    def _handle_undo(self, body: dict) -> None:
        group_id = body.get("group_id")
        if group_id is None:
            self._send_json({"error": "Missing group_id"}, 400)
            return

        self.cache.clear_actions_for_group(group_id)
        self._send_json({"ok": True})

    def _send_summary(self) -> None:
        self._send_json({
            "total_groups": len(self.groups),
            "merged": 0,  # groups are removed on merge, so remaining = unreviewed
        })


def start_server(
    html: str,
    groups: list[DuplicateGroup],
    cache: CacheDB,
    config: Config,
    port: int = 8777,
) -> HTTPServer:
    """Create and return the review HTTP server (call .serve_forever() to run)."""
    items_by_uuid = {
        item.uuid: item for group in groups for item in group.items
    }

    # Set class-level attributes so all handler instances share state
    ReviewHandler.html_content = html
    ReviewHandler.cache = cache
    ReviewHandler.config = config
    ReviewHandler.groups = groups
    ReviewHandler.items_by_uuid = items_by_uuid
    ReviewHandler.thumb_cache = {}

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    return server
