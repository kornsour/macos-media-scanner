"""Tests for similar photo finder."""

from pathlib import Path
from unittest.mock import patch

from media_scanner.config import Config
from media_scanner.core.similar_finder import find_similar_photos
from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MatchType, MediaType
from tests.conftest import sample_item


class TestFindSimilarPhotos:
    @patch("media_scanner.core.similar_finder.hamming_distance")
    @patch("media_scanner.core.similar_finder.dhash_image")
    def test_groups_within_range(self, mock_dhash, mock_hamming, cache: CacheDB):
        """Items in the similar range (threshold+1, threshold*3] are grouped."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10)
        # Distance 15 is within similar range (11, 30]
        mock_hamming.return_value = 15

        with patch.object(Path, "exists", return_value=True):
            groups = find_similar_photos(cache, config)

        assert len(groups) == 1
        assert groups[0].match_type == MatchType.SIMILAR

    @patch("media_scanner.core.similar_finder.hamming_distance")
    @patch("media_scanner.core.similar_finder.dhash_image")
    def test_excludes_exact_range(self, mock_dhash, mock_hamming, cache: CacheDB):
        """Items within the near-duplicate range (<= threshold) are excluded."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10)
        # Distance 5 is within near range, NOT similar range
        mock_hamming.return_value = 5

        with patch.object(Path, "exists", return_value=True):
            groups = find_similar_photos(cache, config)

        assert len(groups) == 0

    @patch("media_scanner.core.similar_finder.hamming_distance")
    @patch("media_scanner.core.similar_finder.dhash_image")
    def test_excludes_too_distant(self, mock_dhash, mock_hamming, cache: CacheDB):
        """Items outside the max distance are not grouped."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10)
        # Distance 50 is outside similar range (11, 30]
        mock_hamming.return_value = 50

        with patch.object(Path, "exists", return_value=True):
            groups = find_similar_photos(cache, config)

        assert len(groups) == 0

    @patch("media_scanner.core.similar_finder.hamming_distance")
    @patch("media_scanner.core.similar_finder.dhash_image")
    def test_custom_distance_range(self, mock_dhash, mock_hamming, cache: CacheDB):
        """Custom min/max distance overrides config-based defaults."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa"),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb"),
        ]
        cache.upsert_items_batch(items)

        config = Config()
        mock_hamming.return_value = 20

        with patch.object(Path, "exists", return_value=True):
            groups = find_similar_photos(
                cache, config, min_distance=15, max_distance=25
            )

        assert len(groups) == 1

    def test_skips_videos(self, cache: CacheDB):
        items = [
            sample_item(uuid="v1", media_type=MediaType.VIDEO, path=Path("/tmp/v1.mov"), dhash="aaa"),
            sample_item(uuid="v2", media_type=MediaType.VIDEO, path=Path("/tmp/v2.mov"), dhash="aaa"),
        ]
        cache.upsert_items_batch(items)

        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_similar_photos(cache, config)

        assert len(groups) == 0
