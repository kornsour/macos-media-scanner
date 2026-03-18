"""SQLite cache manager."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from media_scanner.data.migrations import migrate
from media_scanner.data.models import (
    ActionRecord,
    ActionType,
    DuplicateGroup,
    MatchType,
    MediaItem,
    MediaType,
    MetadataTransfer,
)


def _dt_to_str(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _str_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


class CacheDB:
    """SQLite-backed cache for media metadata and analysis results."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)

    def close(self) -> None:
        self.conn.close()

    # ── Media Items ──────────────────────────────────────────

    def upsert_item(self, item: MediaItem) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO media_items (
                uuid, filename, original_filename, path, media_type,
                file_size, width, height, date_created, date_modified,
                duration, uti, has_gps, latitude, longitude,
                albums, persons, keywords,
                is_edited, is_favorite, is_hidden, is_screenshot,
                is_selfie, is_burst, burst_uuid, live_photo_uuid,
                live_photo_video_path,
                apple_score, sha256, dhash, phash,
                dhash_small, phash_small, motion_score
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                item.uuid,
                item.filename,
                item.original_filename,
                str(item.path) if item.path else None,
                item.media_type.value,
                item.file_size,
                item.width,
                item.height,
                _dt_to_str(item.date_created),
                _dt_to_str(item.date_modified),
                item.duration,
                item.uti,
                int(item.has_gps),
                item.latitude,
                item.longitude,
                json.dumps(item.albums),
                json.dumps(item.persons),
                json.dumps(item.keywords),
                int(item.is_edited),
                int(item.is_favorite),
                int(item.is_hidden),
                int(item.is_screenshot),
                int(item.is_selfie),
                int(item.is_burst),
                item.burst_uuid,
                item.live_photo_uuid,
                str(item.live_photo_video_path) if item.live_photo_video_path else None,
                item.apple_score,
                item.sha256,
                item.dhash,
                item.phash,
                item.dhash_small,
                item.phash_small,
                item.motion_score,
            ),
        )

    def upsert_items_batch(self, items: list[MediaItem]) -> None:
        for item in items:
            self.upsert_item(item)
        self.conn.commit()

    def _row_to_item(self, row: sqlite3.Row) -> MediaItem:
        return MediaItem(
            uuid=row["uuid"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            path=Path(row["path"]) if row["path"] else None,
            media_type=MediaType(row["media_type"]),
            file_size=row["file_size"],
            width=row["width"],
            height=row["height"],
            date_created=_str_to_dt(row["date_created"]),
            date_modified=_str_to_dt(row["date_modified"]),
            duration=row["duration"],
            uti=row["uti"],
            has_gps=bool(row["has_gps"]),
            latitude=row["latitude"],
            longitude=row["longitude"],
            albums=json.loads(row["albums"]),
            persons=json.loads(row["persons"]),
            keywords=json.loads(row["keywords"]),
            is_edited=bool(row["is_edited"]),
            is_favorite=bool(row["is_favorite"]),
            is_hidden=bool(row["is_hidden"]),
            is_screenshot=bool(row["is_screenshot"]),
            is_selfie=bool(row["is_selfie"]),
            is_burst=bool(row["is_burst"]),
            burst_uuid=row["burst_uuid"],
            live_photo_uuid=row["live_photo_uuid"],
            live_photo_video_path=Path(row["live_photo_video_path"]) if row["live_photo_video_path"] else None,
            apple_score=row["apple_score"],
            sha256=row["sha256"],
            dhash=row["dhash"],
            phash=row["phash"],
            dhash_small=row["dhash_small"],
            phash_small=row["phash_small"],
            motion_score=row["motion_score"],
        )

    def get_item(self, uuid: str) -> MediaItem | None:
        row = self.conn.execute(
            "SELECT * FROM media_items WHERE uuid = ?", (uuid,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def get_all_items(self) -> list[MediaItem]:
        rows = self.conn.execute("SELECT * FROM media_items").fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_items_by_type(self, media_type: MediaType) -> list[MediaItem]:
        rows = self.conn.execute(
            "SELECT * FROM media_items WHERE media_type = ?",
            (media_type.value,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_items_with_size(self, file_size: int) -> list[MediaItem]:
        rows = self.conn.execute(
            "SELECT * FROM media_items WHERE file_size = ?", (file_size,)
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_size_groups(self, min_group_size: int = 2) -> dict[int, list[str]]:
        """Return {file_size: [uuids]} for sizes that appear more than once."""
        rows = self.conn.execute(
            """
            SELECT file_size, GROUP_CONCAT(uuid) as uuids
            FROM media_items
            GROUP BY file_size
            HAVING COUNT(*) >= ?
            """,
            (min_group_size,),
        ).fetchall()
        result: dict[int, list[str]] = {}
        for row in rows:
            result[row["file_size"]] = row["uuids"].split(",")
        return result

    def get_duration_groups(
        self, tolerance: float = 2.0, min_group_size: int = 2
    ) -> list[list[MediaItem]]:
        """Group videos by similar duration."""
        rows = self.conn.execute(
            "SELECT * FROM media_items WHERE media_type = 'video' AND duration IS NOT NULL "
            "ORDER BY duration"
        ).fetchall()
        items = [self._row_to_item(r) for r in rows]
        if not items:
            return []

        groups: list[list[MediaItem]] = []
        current_group = [items[0]]
        for item in items[1:]:
            # Compare to the *previous* item (sliding window) rather than
            # the group anchor.  Since items are sorted by duration, this
            # chains nearby durations transitively: 10s-11.5s-13s all end
            # up together when tolerance=2.0s.
            if abs(item.duration - current_group[-1].duration) <= tolerance:
                current_group.append(item)
            else:
                if len(current_group) >= min_group_size:
                    groups.append(current_group)
                current_group = [item]
        if len(current_group) >= min_group_size:
            groups.append(current_group)
        return groups

    def get_live_photos_with_video(self) -> list[MediaItem]:
        """Return live photos that have a local video component path."""
        rows = self.conn.execute(
            "SELECT * FROM media_items "
            "WHERE media_type = 'live_photo' AND live_photo_video_path IS NOT NULL"
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def update_motion_score(self, uuid: str, score: float) -> None:
        self.conn.execute(
            "UPDATE media_items SET motion_score = ? WHERE uuid = ?",
            (score, uuid),
        )
        self.conn.commit()

    def update_hash(self, uuid: str, hash_type: str, hash_value: str) -> None:
        assert hash_type in ("sha256", "dhash", "phash", "dhash_small", "phash_small")
        self.conn.execute(
            f"UPDATE media_items SET {hash_type} = ? WHERE uuid = ?",
            (hash_value, uuid),
        )
        self.conn.commit()

    def update_hashes_batch(
        self, updates: list[tuple[str, str, str]]
    ) -> None:
        """Batch update hashes. Each tuple: (uuid, hash_type, hash_value)."""
        for uuid, hash_type, hash_value in updates:
            assert hash_type in ("sha256", "dhash", "phash", "dhash_small", "phash_small")
            self.conn.execute(
                f"UPDATE media_items SET {hash_type} = ? WHERE uuid = ?",
                (hash_value, uuid),
            )
        self.conn.commit()

    def item_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM media_items").fetchone()
        return row["cnt"]

    def remove_items_not_in(self, valid_uuids: set[str]) -> int:
        """Delete cached items whose UUIDs are no longer in the Photos library.

        Returns the number of rows removed.
        """
        all_rows = self.conn.execute("SELECT uuid FROM media_items").fetchall()
        stale = [r["uuid"] for r in all_rows if r["uuid"] not in valid_uuids]
        if not stale:
            return 0
        batch_size = 500
        for i in range(0, len(stale), batch_size):
            batch = stale[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            self.conn.execute(
                f"DELETE FROM media_items WHERE uuid IN ({placeholders})", batch
            )
            self.conn.execute(
                f"DELETE FROM actions WHERE uuid IN ({placeholders})", batch
            )
            self.conn.execute(
                f"DELETE FROM duplicate_group_members WHERE uuid IN ({placeholders})",
                batch,
            )
        self.conn.commit()
        return len(stale)

    # ── Duplicate Groups ─────────────────────────────────────

    def clear_duplicate_groups(self) -> None:
        self.conn.execute("DELETE FROM duplicate_group_members")
        self.conn.execute("DELETE FROM duplicate_groups")
        self.conn.commit()

    def delete_duplicate_group(self, group_id: int) -> None:
        """Remove a single duplicate group and its members."""
        self.conn.execute(
            "DELETE FROM duplicate_group_members WHERE group_id = ?", (group_id,)
        )
        self.conn.execute(
            "DELETE FROM duplicate_groups WHERE group_id = ?", (group_id,)
        )
        self.conn.commit()

    def save_duplicate_group(self, group: DuplicateGroup) -> int:
        cursor = self.conn.execute(
            "INSERT INTO duplicate_groups (match_type, recommended_keep_uuid) VALUES (?, ?)",
            (group.match_type.value, group.recommended_keep_uuid),
        )
        group_id = cursor.lastrowid
        group.group_id = group_id
        for item in group.items:
            self.conn.execute(
                "INSERT INTO duplicate_group_members (group_id, uuid) VALUES (?, ?)",
                (group_id, item.uuid),
            )
        self.conn.commit()
        return group_id

    def get_duplicate_groups(
        self, match_type: MatchType | None = None
    ) -> list[DuplicateGroup]:
        if match_type:
            rows = self.conn.execute(
                "SELECT * FROM duplicate_groups WHERE match_type = ?",
                (match_type.value,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM duplicate_groups").fetchall()

        groups = []
        for row in rows:
            members = self.conn.execute(
                "SELECT uuid FROM duplicate_group_members WHERE group_id = ?",
                (row["group_id"],),
            ).fetchall()
            items = []
            for m in members:
                item = self.get_item(m["uuid"])
                if item:
                    items.append(item)
            groups.append(
                DuplicateGroup(
                    group_id=row["group_id"],
                    match_type=MatchType(row["match_type"]),
                    items=items,
                    recommended_keep_uuid=row["recommended_keep_uuid"],
                )
            )
        return groups

    # ── Actions ──────────────────────────────────────────────

    def save_action(self, action: ActionRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO actions (uuid, action, group_id, created_at, applied, applied_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action.uuid,
                action.action.value,
                action.group_id,
                _dt_to_str(action.created_at),
                int(action.applied),
                _dt_to_str(action.applied_at),
            ),
        )
        self.conn.commit()

    def get_pending_actions(self, action_type: ActionType | None = None) -> list[ActionRecord]:
        if action_type:
            rows = self.conn.execute(
                "SELECT * FROM actions WHERE applied = 0 AND action = ?",
                (action_type.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM actions WHERE applied = 0"
            ).fetchall()
        return [
            ActionRecord(
                uuid=r["uuid"],
                action=ActionType(r["action"]),
                group_id=r["group_id"],
                created_at=_str_to_dt(r["created_at"]) or datetime.now(),
                applied=bool(r["applied"]),
                applied_at=_str_to_dt(r["applied_at"]),
            )
            for r in rows
        ]

    def mark_actions_applied(self, uuids: list[str]) -> None:
        now = _dt_to_str(datetime.now())
        for uuid in uuids:
            self.conn.execute(
                "UPDATE actions SET applied = 1, applied_at = ? WHERE uuid = ? AND applied = 0",
                (now, uuid),
            )
        self.conn.commit()

    def clear_pending_actions(self) -> None:
        self.conn.execute("DELETE FROM actions WHERE applied = 0")
        self.conn.commit()

    def clear_actions_for_group(self, group_id: int) -> None:
        """Remove all pending actions for a specific group."""
        self.conn.execute(
            "DELETE FROM actions WHERE group_id = ? AND applied = 0",
            (group_id,),
        )
        self.conn.execute(
            "DELETE FROM metadata_transfers WHERE group_id = ? AND applied = 0",
            (group_id,),
        )
        self.conn.commit()

    # ── Metadata Transfers ─────────────────────────────────

    def save_metadata_transfer(self, transfer: MetadataTransfer) -> None:
        self.conn.execute(
            """
            INSERT INTO metadata_transfers (
                keeper_uuid, group_id, transfer_date,
                transfer_latitude, transfer_longitude, source_uuid,
                created_at, applied, applied_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer.keeper_uuid,
                transfer.group_id,
                _dt_to_str(transfer.transfer_date),
                transfer.transfer_latitude,
                transfer.transfer_longitude,
                transfer.source_uuid,
                _dt_to_str(transfer.created_at),
                int(transfer.applied),
                _dt_to_str(transfer.applied_at),
                transfer.error_message,
            ),
        )
        self.conn.commit()

    def get_pending_transfers(self) -> list[MetadataTransfer]:
        rows = self.conn.execute(
            "SELECT * FROM metadata_transfers WHERE applied = 0"
        ).fetchall()
        return [
            MetadataTransfer(
                keeper_uuid=r["keeper_uuid"],
                group_id=r["group_id"],
                transfer_date=_str_to_dt(r["transfer_date"]),
                transfer_latitude=r["transfer_latitude"],
                transfer_longitude=r["transfer_longitude"],
                source_uuid=r["source_uuid"],
                created_at=_str_to_dt(r["created_at"]) or datetime.now(),
                applied=bool(r["applied"]),
                applied_at=_str_to_dt(r["applied_at"]),
                error_message=r["error_message"],
            )
            for r in rows
        ]

    def mark_transfer_applied(
        self, keeper_uuid: str, group_id: int, error: str | None = None
    ) -> None:
        now = _dt_to_str(datetime.now())
        self.conn.execute(
            """
            UPDATE metadata_transfers
            SET applied = 1, applied_at = ?, error_message = ?
            WHERE keeper_uuid = ? AND group_id = ? AND applied = 0
            """,
            (now, error, keeper_uuid, group_id),
        )
        self.conn.commit()

    def clear_pending_transfers(self) -> None:
        self.conn.execute("DELETE FROM metadata_transfers WHERE applied = 0")
        self.conn.commit()

    # ── Scan Metadata ────────────────────────────────────────

    def set_scan_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO scan_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def get_scan_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM scan_metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # ── Stats Queries ────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregate statistics about the cached library."""
        total = self.conn.execute("SELECT COUNT(*) as c FROM media_items").fetchone()["c"]
        photos = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE media_type = 'photo'"
        ).fetchone()["c"]
        videos = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE media_type = 'video'"
        ).fetchone()["c"]
        live = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE media_type = 'live_photo'"
        ).fetchone()["c"]
        total_size = self.conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as s FROM media_items"
        ).fetchone()["s"]
        favorites = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE is_favorite = 1"
        ).fetchone()["c"]
        hidden = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE is_hidden = 1"
        ).fetchone()["c"]
        screenshots = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE is_screenshot = 1"
        ).fetchone()["c"]
        selfies = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE is_selfie = 1"
        ).fetchone()["c"]
        edited = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE is_edited = 1"
        ).fetchone()["c"]
        with_gps = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE has_gps = 1"
        ).fetchone()["c"]
        no_date = self.conn.execute(
            "SELECT COUNT(*) as c FROM media_items WHERE date_created IS NULL"
        ).fetchone()["c"]
        oldest = self.conn.execute(
            "SELECT MIN(date_created) as d FROM media_items WHERE date_created IS NOT NULL"
        ).fetchone()["d"]
        newest = self.conn.execute(
            "SELECT MAX(date_created) as d FROM media_items WHERE date_created IS NOT NULL"
        ).fetchone()["d"]

        # File type distribution
        type_dist = self.conn.execute(
            "SELECT uti, COUNT(*) as c FROM media_items GROUP BY uti ORDER BY c DESC"
        ).fetchall()

        return {
            "total": total,
            "photos": photos,
            "videos": videos,
            "live_photos": live,
            "total_size": total_size,
            "favorites": favorites,
            "hidden": hidden,
            "screenshots": screenshots,
            "selfies": selfies,
            "edited": edited,
            "with_gps": with_gps,
            "no_date": no_date,
            "oldest": oldest,
            "newest": newest,
            "type_distribution": {r["uti"]: r["c"] for r in type_dist},
        }
