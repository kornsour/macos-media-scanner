"""Tests for the multi-stage duplicate detection pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_scanner.config import Config
from media_scanner.core.duplicate_finder import (
    _confirm_with_phash,
    find_exact_duplicates,
    find_live_photo_video_duplicates,
    find_near_duplicates,
    find_video_duplicates,
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
    @patch("media_scanner.core.duplicate_finder.hamming_distance_int")
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
    @patch("media_scanner.core.duplicate_finder.hamming_distance_int")
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


class TestFindVideoDuplicates:
    def test_cloud_only_same_size_groups(self, cache: CacheDB):
        """Cloud-only videos with same duration + file_size => exact group."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.0,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.5,
            ),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_video_duplicates(cache, config)
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.EXACT
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"v1", "v2"}

    def test_cloud_only_different_size_no_group(self, cache: CacheDB):
        """Cloud-only videos with same duration but different file_size => no group."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.0,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO, path=None,
                file_size=60_000_000, duration=30.5,
            ),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_video_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.sha256_video")
    def test_mixed_cloud_and_local_merge(self, mock_sha, cache: CacheDB):
        """Cloud-only video merges into SHA group when file_size matches."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"), file_size=50_000_000, duration=30.0,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.5,
            ),
        ]
        cache.upsert_items_batch(items)
        mock_sha.return_value = "videohash"
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_video_duplicates(cache, config)
        assert len(groups) == 1
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"v1", "v2"}

    @patch("media_scanner.core.duplicate_finder.sha256_video")
    def test_local_videos_exact_match(self, mock_sha, cache: CacheDB):
        """Local videos with same SHA-256 => exact group (existing behavior)."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"), file_size=50_000_000, duration=30.0,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO,
                path=Path("/tmp/v2.mov"), file_size=50_000_000, duration=30.5,
            ),
        ]
        cache.upsert_items_batch(items)
        mock_sha.return_value = "samehash"
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_video_duplicates(cache, config)
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.EXACT

    def test_no_duration_no_group(self, cache: CacheDB):
        """Videos without duration are excluded from duration grouping."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=None,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=None,
            ),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_video_duplicates(cache, config)
        assert len(groups) == 0

    def test_progress_callback_called(self, cache: CacheDB):
        """Progress callback fires for cloud-only videos."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.0,
            ),
            sample_item(
                uuid="v2", media_type=MediaType.VIDEO, path=None,
                file_size=50_000_000, duration=30.5,
            ),
        ]
        cache.upsert_items_batch(items)
        callback = MagicMock()
        config = Config()
        find_video_duplicates(cache, config, progress_callback=callback)
        assert callback.call_count >= 1


class TestFindLivePhotoVideoDuplicates:
    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_exact_match_live_photo_vs_video(self, mock_sha, cache: CacheDB):
        """Live photo .mov with same SHA as standalone video => exact group."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                path=Path("/tmp/lp1.heic"),
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=2.5,
                file_size=3_000_000,
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=2.5,
                file_size=5_000_000,
                sha256="samehash",
            ),
        ]
        cache.upsert_items_batch(items)
        mock_sha.return_value = "samehash"
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_live_photo_video_duplicates(cache, config)
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.EXACT
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"lp1", "v1"}

    def test_no_live_photos_returns_empty(self, cache: CacheDB):
        """No live photos with video paths => empty result."""
        items = [
            sample_item(
                uuid="v1", media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=30.0, file_size=50_000_000,
            ),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_live_photo_video_duplicates(cache, config)
        assert len(groups) == 0

    def test_no_standalone_videos_returns_empty(self, cache: CacheDB):
        """No standalone videos with duration => empty result."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=2.5,
            ),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_live_photo_video_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder._probe_duration")
    def test_duration_mismatch_no_group(self, mock_probe, cache: CacheDB):
        """Live photo and video with very different durations => no match."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=None,
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=60.0,
                file_size=50_000_000,
            ),
        ]
        cache.upsert_items_batch(items)
        mock_probe.return_value = 2.5  # live photo is 2.5s, video is 60s
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_live_photo_video_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_different_sha_no_exact_match(self, mock_sha, cache: CacheDB):
        """Different SHA-256 => no exact group."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=2.5,
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=2.5,
                file_size=5_000_000,
                sha256="video_hash",
            ),
        ]
        cache.upsert_items_batch(items)
        mock_sha.return_value = "live_hash"  # different from video's sha256
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_live_photo_video_duplicates(cache, config, include_near=False)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.video_frames_similar")
    @patch("media_scanner.core.video_hasher.dhash_video")
    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_near_match_with_keyframes(
        self, mock_sha, mock_dhash, mock_similar, cache: CacheDB
    ):
        """Near match via keyframe dHash when include_near=True."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=2.5,
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=2.5,
                file_size=5_000_000,
                sha256="video_hash",
            ),
        ]
        cache.upsert_items_batch(items)
        mock_sha.return_value = "live_hash"  # different from video
        mock_dhash.return_value = ["aa", "bb"]
        mock_similar.return_value = True
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_live_photo_video_duplicates(
                cache, config, include_near=True
            )
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.NEAR

    def test_progress_callback_called(self, cache: CacheDB):
        """Progress callback fires for each live photo processed."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=2.5,
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=2.5,
                file_size=5_000_000,
            ),
        ]
        cache.upsert_items_batch(items)
        callback = MagicMock()
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            find_live_photo_video_duplicates(
                cache, config, progress_callback=callback
            )
        assert callback.call_count >= 1

    @patch("media_scanner.core.duplicate_finder._probe_duration")
    @patch("media_scanner.core.duplicate_finder.sha256_file")
    def test_fallback_duration_probe(self, mock_sha, mock_probe, cache: CacheDB):
        """When duration is None, falls back to ffprobe."""
        items = [
            sample_item(
                uuid="lp1",
                media_type=MediaType.LIVE_PHOTO,
                live_photo_video_path=Path("/tmp/lp1.mov"),
                duration=None,  # not in cache
            ),
            sample_item(
                uuid="v1",
                media_type=MediaType.VIDEO,
                path=Path("/tmp/v1.mov"),
                duration=2.5,
                file_size=5_000_000,
                sha256="samehash",
            ),
        ]
        cache.upsert_items_batch(items)
        mock_probe.return_value = 2.5  # ffprobe returns duration
        mock_sha.return_value = "samehash"
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_live_photo_video_duplicates(cache, config)
        assert len(groups) == 1
        mock_probe.assert_called_once()
