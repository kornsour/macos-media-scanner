"""Local HTTP server for interactive duplicate review."""

from __future__ import annotations

import io
import json
import logging
import math
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from PIL import Image

from media_scanner.ui.report import PAGE_SIZE, RAW_EXTENSIONS, VIDEO_EXTENSIONS, _raw_to_jpeg, _video_frame_jpeg

from media_scanner.actions.applescript import ALBUM_NAME, CORRUPT_ALBUM_NAME, KEEPER_ALBUM_NAME
from media_scanner.core.metadata_merger import compute_transfers
from media_scanner.data.models import ActionRecord, ActionType, MediaType

if TYPE_CHECKING:
    from media_scanner.config import Config
    from media_scanner.data.cache import CacheDB
    from media_scanner.data.models import DuplicateGroup, MediaItem

logger = logging.getLogger(__name__)

def _read_original(item: MediaItem) -> tuple[bytes, str] | None:
    """Read original image/video-thumbnail file bytes and determine MIME type."""
    if not item.path or not item.path.exists():
        return None
    try:
        suffix = item.path.suffix.lower()

        # Video files — extract a single frame as JPEG
        if suffix in VIDEO_EXTENSIONS:
            data = _video_frame_jpeg(item.path, thumb_size=480)
            if data:
                return data, "image/jpeg"
            return None

        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".heic": "image/heic",
            ".heif": "image/heif",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
            ".bmp": "image/bmp",
        }
        mime = mime_map.get(suffix, "image/jpeg")

        # RAW files can't be displayed by browsers — convert to JPEG via sips
        if suffix in RAW_EXTENSIONS:
            data = _raw_to_jpeg(item.path, max_size=480)
            if data:
                return data, "image/jpeg"
            return None

        # HEIC/HEIF can't be displayed by browsers — convert to JPEG
        if suffix in (".heic", ".heif"):
            with Image.open(item.path) as img:
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=92)
                return buf.getvalue(), "image/jpeg"

        data = item.path.read_bytes()
        return data, mime
    except Exception:
        return None


def _apply_photokit(
    delete_uuids: list[str], keep_uuid: str | None, transfers: list[dict]
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
        # 3. Add keeper to the keepers album (if any)
        if keep_uuid:
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
            if keep_uuid:
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
    title: str
    thumb_cache: dict[str, tuple[bytes, str] | None]

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
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            params = parse_qs(parsed.query)
            page = max(1, int(params.get("page", ["1"])[0]))
            per_page = max(10, min(500, int(params.get("per_page", [str(PAGE_SIZE)])[0])))
            active_filter = params.get("filter", ["all"])[0]
            sort_order = params.get("sort", ["default"])[0]

            from media_scanner.ui.report import group_tags

            # Filter groups server-side if a category is active
            if active_filter and active_filter != "all":
                filtered = [g for g in self.groups if active_filter in group_tags(g)]
            else:
                filtered = self.groups

            # Sort groups
            if sort_order == "most_items":
                filtered = sorted(filtered, key=lambda g: len(g.items), reverse=True)
            elif sort_order == "least_items":
                filtered = sorted(filtered, key=lambda g: len(g.items))

            total_groups = len(filtered)
            total_pages = max(1, math.ceil(total_groups / per_page))
            page = min(page, total_pages)

            start = (page - 1) * per_page
            page_groups = filtered[start:start + per_page]

            pending = self.cache.get_pending_actions()
            actions = {a.uuid: a for a in pending}

            from media_scanner.ui.report import generate_page_html

            html = generate_page_html(
                page_groups, self.config, page, total_pages, total_groups,
                actions=actions, title=self.title, per_page=per_page,
                active_filter=active_filter, sort_order=sort_order,
            )

            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith("/thumb/"):
            uuid = path[7:]
            self._serve_thumbnail(uuid)

        elif path.startswith("/video/"):
            uuid = path[7:]
            self._serve_video(uuid)

        elif path == "/api/summary":
            self._send_summary()

        elif path == "/api/all-groups":
            self._send_all_groups()

        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/merge":
            self._handle_merge(body)
        elif path == "/api/undo":
            self._handle_undo(body)
        elif path == "/api/flag-corrupt":
            self._handle_flag_corrupt(body)
        else:
            self.send_error(404)

    def _serve_thumbnail(self, uuid: str) -> None:
        if uuid in self.thumb_cache:
            result = self.thumb_cache[uuid]
        else:
            item = self.items_by_uuid.get(uuid)
            result = _read_original(item) if item else None
            self.thumb_cache[uuid] = result

        if result:
            data, mime = result
            try:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
            except BrokenPipeError:
                pass  # Browser closed connection before thumbnail was sent
        else:
            self.send_error(404)

    def _serve_video(self, uuid: str) -> None:
        """Serve video file with HTTP Range support for seeking."""
        item = self.items_by_uuid.get(uuid)
        if not item or not item.path or not item.path.exists():
            self.send_error(404)
            return

        suffix = item.path.suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            self.send_error(404)
            return

        mime_map = {
            ".mov": "video/quicktime",
            ".mp4": "video/mp4",
            ".m4v": "video/x-m4v",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
        }
        mime = mime_map.get(suffix, "video/mp4")
        file_size = item.path.stat().st_size
        chunk_size = 1024 * 1024  # 1MB chunks

        range_header = self.headers.get("Range")
        try:
            if range_header:
                # Parse Range: bytes=start-end
                range_spec = range_header.replace("bytes=", "").strip()
                parts = range_spec.split("-")
                start = int(parts[0])
                end = int(parts[1]) if parts[1] else min(start + chunk_size - 1, file_size - 1)
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()

                with open(item.path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        to_read = min(chunk_size, remaining)
                        data = f.read(to_read)
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(file_size))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()

                with open(item.path, "rb") as f:
                    while True:
                        data = f.read(chunk_size)
                        if not data:
                            break
                        self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client closed connection

    def _handle_merge(self, body: dict) -> None:
        group_id = body.get("group_id")
        # Support both keep_uuids (multi-select) and legacy keep_uuid (single)
        keep_uuids = body.get("keep_uuids") or []
        if not keep_uuids and body.get("keep_uuid"):
            keep_uuids = [body["keep_uuid"]]

        if group_id is None:
            self._send_json({"error": "Missing group_id"}, 400)
            return

        keep_uuids_set = set(keep_uuids)

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
            is_keep = item.uuid in keep_uuids_set
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

        # Apply via PhotoKit
        if delete_uuids:
            first_keeper = keep_uuids[0] if keep_uuids else None
            pk_result = _apply_photokit(delete_uuids, first_keeper, transfer_payload)
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
            # First keeper already added to keepers album by _apply_photokit
            extra_keepers = keep_uuids[1:] if keep_uuids else []
        else:
            # All kept — just add them all to the keepers album
            extra_keepers = keep_uuids

        if extra_keepers:
            from media_scanner.actions.photokit import create_deletion_album_photokit

            keeper_result = create_deletion_album_photokit(extra_keepers, KEEPER_ALBUM_NAME)
            if not keeper_result["success"]:
                logger.warning("Failed to add keepers to album: %s", keeper_result.get("error"))

        # Success — save actions as applied, clean up cache
        self.cache.clear_actions_for_group(group_id)
        for action in actions:
            action.applied = True
            self.cache.save_action(action)
        for t in transfers:
            self.cache.save_metadata_transfer(t)

        # Remove the group from the duplicate_groups table
        self.cache.delete_duplicate_group(group_id)

        # Remove from the class-level list so subsequent requests see the change
        ReviewHandler.groups = [g for g in ReviewHandler.groups if g.group_id != group_id]

        self._send_json({
            "ok": True,
            "keep_uuids": keep_uuids,
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

    def _handle_flag_corrupt(self, body: dict) -> None:
        """Add suspect corrupt videos to a Photos album for review."""
        threshold = self.config.corrupt_motion_threshold

        if body.get("all"):
            uuids = [
                item.uuid
                for item in self.items_by_uuid.values()
                if item.motion_score is not None
                and item.motion_score <= threshold
                and item.media_type in (MediaType.VIDEO, MediaType.LIVE_PHOTO)
            ]
        else:
            uuids = body.get("uuids", [])

        if not uuids:
            self._send_json({"ok": True, "count": 0})
            return

        from media_scanner.actions.photokit import create_deletion_album_photokit

        result = create_deletion_album_photokit(uuids, CORRUPT_ALBUM_NAME)
        if result["success"]:
            self._send_json({"ok": True, "count": len(uuids)})
        else:
            error = result.get("error", "unknown")
            if error == "auth_denied":
                self._send_json({
                    "ok": False,
                    "error": "Photos access denied. Open System Settings → "
                             "Privacy & Security → Photos and enable PhotosBridge.",
                }, 403)
            else:
                self._send_json({"ok": False, "error": error}, 500)

    def _send_summary(self) -> None:
        from collections import Counter

        from media_scanner.ui.report import group_tags

        total = len(self.groups)
        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        counts: Counter[str] = Counter()
        for g in self.groups:
            for tag in group_tags(g):
                counts[tag] += 1
        self._send_json({
            "total_groups": total,
            "total_pages": total_pages,
            "category_counts": dict(counts),
        })

    def _send_all_groups(self) -> None:
        """Return all group IDs, their recommended keepers, and tags."""
        from media_scanner.ui.report import group_tags

        groups_data = []
        for g in self.groups:
            keep_uuids = [g.recommended_keep_uuid] if g.recommended_keep_uuid else []
            groups_data.append({
                "group_id": g.group_id,
                "keep_uuids": keep_uuids,
                "item_count": len(g.items),
                "tags": group_tags(g),
            })
        self._send_json({"groups": groups_data})


class BrowseHandler(BaseHTTPRequestHandler):
    """HTTP handler for the library browse server."""

    cache: CacheDB
    config: Config
    all_items: list[MediaItem]
    items_by_uuid: dict[str, MediaItem]
    category_counts: dict[str, int]
    actioned_uuids: set[str]
    title: str
    thumb_cache: dict[str, tuple[bytes, str] | None]

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
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_browse_page(parsed)
        elif path.startswith("/thumb/"):
            uuid = path[7:]
            self._serve_thumbnail(uuid)
        elif path.startswith("/video/"):
            uuid = path[7:]
            self._serve_video(uuid)
        elif path.startswith("/live-video/"):
            uuid = path[12:]
            self._serve_live_video(uuid)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/delete":
            self._handle_album_action(body, ALBUM_NAME)
        elif path == "/api/keep":
            self._handle_album_action(body, KEEPER_ALBUM_NAME)
        elif path == "/api/bulk-action":
            self._handle_bulk_action(body)
        else:
            self.send_error(404)

    def _serve_browse_page(self, parsed) -> None:
        from media_scanner.ui.report import (
            BROWSE_CATEGORIES,
            _item_browse_tags,
            generate_browse_page_html,
        )

        params = parse_qs(parsed.query)
        page = max(1, int(params.get("page", ["1"])[0]))
        per_page = max(10, min(500, int(params.get("per_page", ["100"])[0])))
        active_filter = params.get("filter", ["all"])[0]
        sort_order = params.get("sort", ["default"])[0]

        # Exclude already-actioned items
        available = [
            item for item in self.all_items
            if item.uuid not in self.actioned_uuids
        ]

        # Filter items by category
        if active_filter and active_filter != "all":
            filtered = [
                item for item in available
                if active_filter in _item_browse_tags(item)
            ]
        else:
            filtered = available

        # Sort items
        if sort_order == "oldest":
            filtered = sorted(
                filtered,
                key=lambda i: i.date_created or datetime.min,
            )
        elif sort_order == "default":
            filtered = sorted(
                filtered,
                key=lambda i: i.date_created or datetime.min,
                reverse=True,
            )
        elif sort_order == "largest":
            filtered = sorted(filtered, key=lambda i: i.file_size, reverse=True)
        elif sort_order == "smallest":
            filtered = sorted(filtered, key=lambda i: i.file_size)
        elif sort_order == "name":
            filtered = sorted(filtered, key=lambda i: i.filename.lower())

        # Recompute category counts excluding actioned items
        from collections import Counter

        live_counts: Counter[str] = Counter()
        for item in available:
            for tag in _item_browse_tags(item):
                live_counts[tag] += 1

        total_items = len(filtered)
        total_available = len(available)
        total_pages = max(1, math.ceil(total_items / per_page))
        page = min(page, total_pages)

        start = (page - 1) * per_page
        page_items = filtered[start:start + per_page]

        html = generate_browse_page_html(
            page_items,
            page,
            total_pages,
            total_items,
            dict(live_counts),
            per_page=per_page,
            active_filter=active_filter,
            sort_order=sort_order,
            title=self.title,
            total_available=total_available,
        )

        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_thumbnail(self, uuid: str) -> None:
        if uuid in self.thumb_cache:
            result = self.thumb_cache[uuid]
        else:
            item = self.items_by_uuid.get(uuid)
            result = _read_original(item) if item else None
            self.thumb_cache[uuid] = result

        if result:
            data, mime = result
            try:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
            except BrokenPipeError:
                pass
        else:
            self.send_error(404)

    def _serve_video(self, uuid: str) -> None:
        """Serve video file with HTTP Range support."""
        item = self.items_by_uuid.get(uuid)
        if not item or not item.path or not item.path.exists():
            self.send_error(404)
            return

        suffix = item.path.suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            self.send_error(404)
            return

        mime_map = {
            ".mov": "video/quicktime",
            ".mp4": "video/mp4",
            ".m4v": "video/x-m4v",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
        }
        mime = mime_map.get(suffix, "video/mp4")
        file_size = item.path.stat().st_size
        chunk_size = 1024 * 1024

        range_header = self.headers.get("Range")
        try:
            if range_header:
                range_spec = range_header.replace("bytes=", "").strip()
                parts = range_spec.split("-")
                start = int(parts[0])
                end = int(parts[1]) if parts[1] else min(start + chunk_size - 1, file_size - 1)
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()

                with open(item.path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        to_read = min(chunk_size, remaining)
                        data = f.read(to_read)
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(file_size))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()

                with open(item.path, "rb") as f:
                    while True:
                        data = f.read(chunk_size)
                        if not data:
                            break
                        self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_live_video(self, uuid: str) -> None:
        """Serve the .mov component of a live photo."""
        item = self.items_by_uuid.get(uuid)
        if not item or not item.live_photo_video_path or not item.live_photo_video_path.exists():
            self.send_error(404)
            return

        video_path = item.live_photo_video_path
        mime = "video/quicktime"
        file_size = video_path.stat().st_size

        try:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()

            with open(video_path, "rb") as f:
                while True:
                    data = f.read(1024 * 1024)
                    if not data:
                        break
                    self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _record_action(self, uuid: str, action_type: ActionType, applied: bool) -> None:
        """Save an action to the DB and mark UUID as actioned."""
        self.actioned_uuids.add(uuid)
        action = ActionRecord(
            uuid=uuid,
            action=action_type,
            group_id=None,
            applied=applied,
            applied_at=datetime.now() if applied else None,
        )
        self.cache.save_action(action)

    def _handle_album_action(self, body: dict, album_name: str) -> None:
        """Add a single item to the specified Photos album."""
        uuid = body.get("uuid")
        if not uuid:
            self._send_json({"error": "Missing uuid"}, 400)
            return

        if uuid not in self.items_by_uuid:
            self._send_json({"error": "Item not found"}, 404)
            return

        action_type = ActionType.DELETE if album_name == ALBUM_NAME else ActionType.KEEP

        from media_scanner.actions.photokit import create_deletion_album_photokit

        result = create_deletion_album_photokit([uuid], album_name)
        if result["success"]:
            self._record_action(uuid, action_type, applied=True)
            self._send_json({"ok": True})
        else:
            error = result.get("error", "unknown")
            if error == "auth_denied":
                self._send_json({
                    "ok": False,
                    "error": "Photos access denied. Open System Settings → "
                             "Privacy & Security → Photos and enable PhotosBridge.",
                }, 403)
            else:
                # PhotoKit couldn't find the asset (e.g. iCloud-only) —
                # record the decision locally so the item is marked as reviewed
                self._record_action(uuid, action_type, applied=False)
                self._send_json({"ok": True, "local_only": True})

    def _handle_bulk_action(self, body: dict) -> None:
        """Add multiple items to Delete or Keep album in one request."""
        uuids = body.get("uuids", [])
        action = body.get("action")  # "delete" or "keep"

        if not uuids or action not in ("delete", "keep"):
            self._send_json({"error": "Missing uuids or invalid action"}, 400)
            return

        # Filter to valid UUIDs only
        valid_uuids = [u for u in uuids if u in self.items_by_uuid]
        if not valid_uuids:
            self._send_json({"error": "No valid UUIDs"}, 404)
            return

        album_name = ALBUM_NAME if action == "delete" else KEEPER_ALBUM_NAME
        action_type = ActionType.DELETE if action == "delete" else ActionType.KEEP

        from media_scanner.actions.photokit import create_deletion_album_photokit

        result = create_deletion_album_photokit(valid_uuids, album_name)
        if result["success"]:
            for uuid in valid_uuids:
                self._record_action(uuid, action_type, applied=True)
            self._send_json({"ok": True, "count": len(valid_uuids)})
        else:
            error = result.get("error", "unknown")
            if error == "auth_denied":
                self._send_json({
                    "ok": False,
                    "error": "Photos access denied. Open System Settings → "
                             "Privacy & Security → Photos and enable PhotosBridge.",
                }, 403)
            else:
                # PhotoKit failed (e.g. iCloud-only batch) —
                # record all decisions locally so items are marked as reviewed
                for uuid in valid_uuids:
                    self._record_action(uuid, action_type, applied=False)
                self._send_json({"ok": True, "count": len(valid_uuids), "local_only": True})


def start_browse_server(
    items: list[MediaItem],
    cache: CacheDB,
    config: Config,
    port: int = 8778,
    title: str = "Library Browser",
) -> HTTPServer:
    """Create and return the browse HTTP server."""
    from collections import Counter

    from media_scanner.ui.report import _item_browse_tags

    items_by_uuid = {item.uuid: item for item in items}

    category_counts: Counter[str] = Counter()
    for item in items:
        for tag in _item_browse_tags(item):
            category_counts[tag] += 1

    BrowseHandler.title = title
    BrowseHandler.cache = cache
    BrowseHandler.config = config
    BrowseHandler.all_items = items
    BrowseHandler.items_by_uuid = items_by_uuid
    BrowseHandler.category_counts = dict(category_counts)

    # Restore previously actioned items from the DB (both applied and local-only)
    all_actions = cache.conn.execute(
        "SELECT uuid FROM actions"
    ).fetchall()
    BrowseHandler.actioned_uuids = {row["uuid"] for row in all_actions}

    BrowseHandler.thumb_cache = {}

    server = HTTPServer(("127.0.0.1", port), BrowseHandler)
    return server


def start_server(
    groups: list[DuplicateGroup],
    cache: CacheDB,
    config: Config,
    port: int = 8777,
    title: str = "Duplicate Review",
) -> HTTPServer:
    """Create and return the review HTTP server (call .serve_forever() to run)."""
    items_by_uuid = {
        item.uuid: item for group in groups for item in group.items
    }

    # Set class-level attributes so all handler instances share state
    ReviewHandler.title = title
    ReviewHandler.cache = cache
    ReviewHandler.config = config
    ReviewHandler.groups = groups
    ReviewHandler.items_by_uuid = items_by_uuid
    ReviewHandler.thumb_cache = {}

    # Share full group list with report module for sidebar counts
    import media_scanner.ui.report as report_mod
    report_mod.ReviewHandler_all_groups = groups

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    return server
