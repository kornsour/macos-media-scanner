"""Tests for file exporter."""

from pathlib import Path

from media_scanner.actions.exporter import export_keepers
from tests.conftest import sample_item


class TestExportKeepers:
    def test_copies_files(self, tmp_path: Path):
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        src_file = src_dir / "IMG_001.heic"
        src_file.write_text("photo data")

        dest_dir = tmp_path / "export"
        items = [sample_item(uuid="a", filename="IMG_001.heic", path=src_file)]

        count = export_keepers(items, dest_dir)

        assert count == 1
        assert (dest_dir / "IMG_001.heic").exists()
        assert (dest_dir / "IMG_001.heic").read_text() == "photo data"

    def test_handles_name_collisions(self, tmp_path: Path):
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        f1 = src_dir / "a.jpg"
        f1.write_text("file1")
        f2 = src_dir / "b.jpg"
        f2.write_text("file2")

        dest_dir = tmp_path / "export"
        # Both items have the same filename but different paths
        items = [
            sample_item(uuid="a", filename="photo.jpg", path=f1),
            sample_item(uuid="b", filename="photo.jpg", path=f2),
        ]

        count = export_keepers(items, dest_dir)

        assert count == 2
        assert (dest_dir / "photo.jpg").exists()
        assert (dest_dir / "photo_1.jpg").exists()

    def test_skips_missing_paths(self, tmp_path: Path):
        dest_dir = tmp_path / "export"
        items = [
            sample_item(uuid="a", path=None),
            sample_item(uuid="b", path=Path("/nonexistent/file.jpg")),
        ]

        count = export_keepers(items, dest_dir)
        assert count == 0

    def test_creates_dest_dir(self, tmp_path: Path):
        src = tmp_path / "src" / "file.jpg"
        src.parent.mkdir()
        src.write_text("data")

        dest = tmp_path / "nested" / "export" / "dir"
        items = [sample_item(uuid="a", filename="file.jpg", path=src)]

        export_keepers(items, dest)
        assert dest.exists()

    def test_empty_list(self, tmp_path: Path):
        dest_dir = tmp_path / "export"
        count = export_keepers([], dest_dir)
        assert count == 0

    def test_preserves_file_content(self, tmp_path: Path):
        src = tmp_path / "original.png"
        content = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
        src.write_bytes(content)

        dest = tmp_path / "exported"
        items = [sample_item(uuid="a", filename="original.png", path=src)]
        export_keepers(items, dest)

        assert (dest / "original.png").read_bytes() == content
