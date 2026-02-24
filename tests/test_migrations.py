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
