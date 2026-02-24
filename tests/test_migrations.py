"""Tests for database schema migrations."""

import sqlite3

from media_scanner.data.migrations import CURRENT_VERSION, MIGRATIONS, get_version, migrate


class TestGetVersion:
    def test_empty_db_returns_zero(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        assert get_version(conn) == 0
        conn.close()

    def test_after_migration(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        migrate(conn)
        assert get_version(conn) == CURRENT_VERSION
        conn.close()


class TestMigrate:
    def test_creates_all_tables(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        migrate(conn)

        tables = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "schema_version",
            "media_items",
            "duplicate_groups",
            "duplicate_group_members",
            "actions",
            "scan_metadata",
            "metadata_transfers",
        }
        assert expected.issubset(tables)
        conn.close()

    def test_creates_indexes(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        migrate(conn)

        indexes = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND name IS NOT NULL"
            ).fetchall()
        }
        expected_indexes = {
            "idx_file_size",
            "idx_sha256",
            "idx_media_type",
            "idx_date_created",
        }
        assert expected_indexes.issubset(indexes)
        conn.close()

    def test_idempotent(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        migrate(conn)
        migrate(conn)  # should not raise
        assert get_version(conn) == CURRENT_VERSION
        conn.close()

    def test_version_stored(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        migrate(conn)

        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == CURRENT_VERSION
        conn.close()

    def test_migration_dict_has_current_version(self):
        assert CURRENT_VERSION in MIGRATIONS
        assert len(MIGRATIONS[CURRENT_VERSION]) > 0

    def test_v2_creates_metadata_transfers(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        migrate(conn)

        # Verify metadata_transfers table exists and has expected columns
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(metadata_transfers)").fetchall()
        }
        expected_columns = {
            "id", "keeper_uuid", "group_id", "transfer_date",
            "transfer_latitude", "transfer_longitude", "source_uuid",
            "created_at", "applied", "applied_at", "error_message",
        }
        assert expected_columns == columns
        conn.close()

    def test_incremental_v1_to_v2(self, tmp_path):
        """Simulate a v1 database upgrading to v2."""
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row

        # Apply only v1 migrations
        for sql in MIGRATIONS[1]:
            conn.execute(sql)
        conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (1,))
        conn.commit()

        assert get_version(conn) == 1

        # Now run migrate() which should apply v2
        migrate(conn)
        assert get_version(conn) == CURRENT_VERSION

        tables = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "metadata_transfers" in tables
        conn.close()
