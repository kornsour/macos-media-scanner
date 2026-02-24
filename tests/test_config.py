"""Tests for Config dataclass."""

from pathlib import Path

from media_scanner.config import Config, DEFAULT_BASE_DIR, DEFAULT_DB_PATH


class TestConfig:
    def test_default_db_path(self):
        c = Config()
        assert c.db_path == DEFAULT_DB_PATH

    def test_default_base_dir(self):
        assert DEFAULT_BASE_DIR == Path.home() / ".media-scanner"

    def test_custom_db_path(self, tmp_path: Path):
        c = Config(db_path=tmp_path / "custom.db")
        assert c.db_path == tmp_path / "custom.db"

    def test_default_photos_library_is_none(self):
        c = Config()
        assert c.photos_library is None

    def test_default_verbose_is_false(self):
        c = Config()
        assert c.verbose is False

    def test_default_thresholds(self):
        c = Config()
        assert c.dhash_threshold == 10
        assert c.phash_threshold == 12
        assert c.video_duration_tolerance == 2.0

    def test_quality_weights_sum_to_one(self):
        c = Config()
        total = sum(c.quality_weights.values())
        assert abs(total - 1.0) < 1e-9

    def test_quality_weights_keys(self):
        c = Config()
        expected = {
            "resolution",
            "format",
            "file_size",
            "metadata",
            "date_originality",
            "apple_score",
            "edit_status",
        }
        assert set(c.quality_weights.keys()) == expected

    def test_ensure_dirs_creates_parent(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "cache.db"
        c = Config(db_path=nested)
        c.ensure_dirs()
        assert nested.parent.exists()

    def test_ensure_dirs_idempotent(self, tmp_path: Path):
        c = Config(db_path=tmp_path / "cache.db")
        c.ensure_dirs()
        c.ensure_dirs()  # should not raise
        assert c.db_path.parent.exists()
