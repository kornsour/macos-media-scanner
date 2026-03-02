"""Database schema versioning."""

from __future__ import annotations

import sqlite3

CURRENT_VERSION = 3

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS media_items (
            uuid TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            path TEXT,
            media_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            date_created TEXT,
            date_modified TEXT,
            duration REAL,
            uti TEXT NOT NULL DEFAULT '',
            has_gps INTEGER NOT NULL DEFAULT 0,
            latitude REAL,
            longitude REAL,
            albums TEXT NOT NULL DEFAULT '[]',
            persons TEXT NOT NULL DEFAULT '[]',
            keywords TEXT NOT NULL DEFAULT '[]',
            is_edited INTEGER NOT NULL DEFAULT 0,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            is_hidden INTEGER NOT NULL DEFAULT 0,
            is_screenshot INTEGER NOT NULL DEFAULT 0,
            is_selfie INTEGER NOT NULL DEFAULT 0,
            is_burst INTEGER NOT NULL DEFAULT 0,
            burst_uuid TEXT,
            live_photo_uuid TEXT,
            apple_score REAL,
            sha256 TEXT,
            dhash TEXT,
            phash TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_file_size ON media_items(file_size)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sha256 ON media_items(sha256)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(media_type)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_date_created ON media_items(date_created)
        """,
        """
        CREATE TABLE IF NOT EXISTS duplicate_groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_type TEXT NOT NULL,
            recommended_keep_uuid TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS duplicate_group_members (
            group_id INTEGER NOT NULL,
            uuid TEXT NOT NULL,
            PRIMARY KEY (group_id, uuid),
            FOREIGN KEY (group_id) REFERENCES duplicate_groups(group_id),
            FOREIGN KEY (uuid) REFERENCES media_items(uuid)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT NOT NULL,
            action TEXT NOT NULL,
            group_id INTEGER,
            created_at TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT,
            FOREIGN KEY (uuid) REFERENCES media_items(uuid)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scan_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    ],
    2: [
        """
        CREATE TABLE IF NOT EXISTS metadata_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keeper_uuid TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            transfer_date TEXT,
            transfer_latitude REAL,
            transfer_longitude REAL,
            source_uuid TEXT,
            created_at TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0,
            applied_at TEXT,
            error_message TEXT,
            FOREIGN KEY (keeper_uuid) REFERENCES media_items(uuid)
        )
        """,
    ],
    3: [
        """
        ALTER TABLE media_items ADD COLUMN live_photo_video_path TEXT
        """,
    ],
}


def get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> None:
    current = get_version(conn)
    for ver in range(current + 1, CURRENT_VERSION + 1):
        for sql in MIGRATIONS[ver]:
            conn.execute(sql)
        conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (ver,))
    conn.commit()
