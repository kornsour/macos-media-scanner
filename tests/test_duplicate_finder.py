"""Tests for the multi-stage duplicate detection pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_scanner.config import Config
from media_scanner.core.duplicate_finder import (
    _confirm_with_phash,
    find_exact_duplicates,
    find_near_duplicates,
)
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MatchType, MediaType
from tests.conftest import sample_item


class TestFindExactDuplicates:
    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_groups_by_same_hash(self, mock_sha, cache: CacheDB):
        """Items with same size AND same SHA-256 => one exact group."""
        items = [
            sample_item(uuid="a", file_size=1000, path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", file_size=1000, path=Path("/tmp/b.jpg")),
            sample_item(uuid="c", file_size=2000, path=Path("/tmp/c.jpg")),
        ]
        cache.upsert_items_batch(items)

        mock_sha.return_value = "samehash"

        with patch.object(Path, "exists", return_value=True):
            groups = find_exact_duplicates(cache)

        assert len(groups) == 1
        assert groups[0].match_type == MatchType.EXACT
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"a", "b"}

    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_different_hashes_no_group(self, mock_sha, cache: CacheDB):
        """Same size but different SHA => no duplicates."""
        items = [
            sample_item(uuid="a", file_size=1000, path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", file_size=1000, path=Path("/tmp/b.jpg")),
        ]
        cache.upsert_items_batch(items)

        mock_sha.side_effect = lambda p, **kw: f"hash-{p}"

        with patch.object(Path, "exists", return_value=True):
            groups = find_exact_duplicates(cache)

        assert len(groups) == 0

    def test_uses_cached_sha256(self, cache: CacheDB):
        """If sha256 is already set, should not call sha256_file."""
        items = [
            sample_item(uuid="a", file_size=1000, sha256="hash1", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", file_size=1000, sha256="hash1", path=Path("/tmp/b.jpg")),
        ]
        cache.upsert_items_batch(items)

        with patch.object(Path, "exists", return_value=True), \
             patch("media_scanner.core.duplicate_finder.sha256_file") as mock_sha:
            groups = find_exact_duplicates(cache)

        mock_sha.assert_not_called()
        assert len(groups) == 1

    def test_singletons_excluded(self, cache: CacheDB):
        """Items with unique file sizes are never grouped."""
        items = [
            sample_item(uuid="a", file_size=1000),
            sample_item(uuid="b", file_size=2000),
            sample_item(uuid="c", file_size=3000),
        ]
        cache.upsert_items_batch(items)
        groups = find_exact_duplicates(cache)
        assert len(groups) == 0

    def test_progress_callback(self, cache: CacheDB):
        """Verifies progress callback is invoked."""
        items = [
            sample_item(uuid="a", file_size=1000, sha256="h", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", file_size=1000, sha256="h", path=Path("/tmp/b.jpg")),
        ]
        cache.upsert_items_batch(items)

        callback = MagicMock()
        with patch.object(Path, "exists", return_value=True):
            find_exact_duplicates(cache, progress_callback=callback)
        assert callback.call_count >= 1


class TestFindNearDuplicates:
    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.hamming_distance")
    def test_groups_similar_dhashes(self, mock_hamming, mock_dhash, mock_confirm, cache: CacheDB):
        """Items with dhash distance <= threshold => near group."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10)
        mock_hamming.return_value = 5  # within threshold
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates

        with patch.object(Path, "exists", return_value=True):
            groups = find_near_duplicates(cache, config)

        assert len(groups) == 1
        assert groups[0].match_type == MatchType.NEAR

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.hamming_distance")
    def test_excludes_distant_dhashes(self, mock_hamming, mock_dhash, cache: CacheDB):
        """Items with dhash distance > threshold => no group."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10)
        mock_hamming.return_value = 50  # outside threshold

        with patch.object(Path, "exists", return_value=True):
            groups = find_near_duplicates(cache, config)

        assert len(groups) == 0

    def test_skips_videos(self, cache: CacheDB):
        """Near dupe finder only processes photos and live photos."""
        items = [
            sample_item(uuid="v1", media_type=MediaType.VIDEO, path=Path("/tmp/v1.mov"), dhash="aaa"),
            sample_item(uuid="v2", media_type=MediaType.VIDEO, path=Path("/tmp/v2.mov"), dhash="aaa"),
        ]
        cache.upsert_items_batch(items)

        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_near_duplicates(cache, config)

        assert len(groups) == 0


class TestConfirmWithPhash:
    @patch("media_scanner.core.duplicate_finder.hamming_distance")
    def test_keeps_close_phash(self, mock_hamming):
        config = Config(phash_threshold=12)
        items = [
            sample_item(uuid="a", phash="aaa", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", phash="bbb", path=Path("/tmp/b.jpg")),
        ]
        mock_hamming.return_value = 5  # within threshold

        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 2

    @patch("media_scanner.core.duplicate_finder.hamming_distance")
    def test_removes_distant_phash(self, mock_hamming):
        config = Config(phash_threshold=12)
        items = [
            sample_item(uuid="a", phash="aaa", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", phash="bbb", path=Path("/tmp/b.jpg")),
        ]
        mock_hamming.return_value = 50  # outside threshold

        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 1

    def test_no_phash_on_anchor_returns_all(self):
        config = Config()
        items = [
            sample_item(uuid="a", phash=None),
            sample_item(uuid="b", phash="bbb"),
        ]
        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 2
