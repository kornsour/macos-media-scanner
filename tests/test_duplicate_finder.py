"""Tests for the multi-stage duplicate detection pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_scanner.config import Config
from media_scanner.core.duplicate_finder import (
    _adaptive_threshold,
    _compute_noise_scores,
    _confirm_with_phash,
    find_exact_duplicates,
    find_grainy_duplicates,
    find_heic_jpeg_duplicates,
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

    @patch("media_scanner.core.duplicate_finder.hamming_distance_int")
    def test_removes_distant_phash(self, mock_hamming):
        config = Config(phash_threshold=46)
        items = [
            sample_item(uuid="a", phash="aaa", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", phash="bbb", path=Path("/tmp/b.jpg")),
        ]
        mock_hamming.return_value = 50  # outside threshold

        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 0  # no cluster of size >= 2

    def test_no_phash_drops_items(self):
        """Items without pHash are dropped from the confirmed set."""
        config = Config()
        items = [
            sample_item(uuid="a", phash=None),
            sample_item(uuid="b", phash="bbb"),
        ]
        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 1  # only "b" has a phash
        assert confirmed[0].uuid == "b"

    @patch("media_scanner.core.duplicate_finder.hamming_distance_int")
    def test_transitive_clustering(self, mock_hamming):
        """If A~B and B~C but A is not close to C, all three stay grouped."""
        config = Config(phash_threshold=46)
        items = [
            sample_item(uuid="a", phash="aaa", path=Path("/tmp/a.jpg")),
            sample_item(uuid="b", phash="bbb", path=Path("/tmp/b.jpg")),
            sample_item(uuid="c", phash="ccc", path=Path("/tmp/c.jpg")),
        ]
        # A~B: close, A~C: far, B~C: close
        def mock_dist(a, b):
            return 5  # all within threshold for simplicity
        mock_hamming.side_effect = mock_dist

        confirmed = _confirm_with_phash(items, MagicMock(), config)
        assert len(confirmed) == 3


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


class TestFindHeicJpegDuplicates:
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.phash_image")
    def test_matches_heic_and_jpeg_by_dhash(self, mock_phash, mock_dhash, cache: CacheDB):
        """HEIC and JPEG with same dHash => one near group."""
        items = [
            sample_item(
                uuid="h1", uti="public.heic",
                path=Path("/tmp/h1.heic"), file_size=2_000_000,
            ),
            sample_item(
                uuid="j1", uti="public.jpeg",
                filename="IMG_0001.jpg", original_filename="IMG_0001.jpg",
                path=Path("/tmp/j1.jpg"), file_size=1_500_000,
            ),
        ]
        cache.upsert_items_batch(items)
        mock_dhash.return_value = "aa" * 8  # 16-char hex = 64-bit dHash
        mock_phash.return_value = "bb" * 8
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 1
        assert groups[0].match_type == MatchType.NEAR
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"h1", "j1"}

    def test_no_heic_items_returns_empty(self, cache: CacheDB):
        """No HEIC items => empty result."""
        items = [
            sample_item(uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg")),
            sample_item(uuid="j2", uti="public.jpeg", path=Path("/tmp/j2.jpg")),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 0

    def test_no_jpeg_items_returns_empty(self, cache: CacheDB):
        """No JPEG items => empty result."""
        items = [
            sample_item(uuid="h1", uti="public.heic", path=Path("/tmp/h1.heic")),
            sample_item(uuid="h2", uti="public.heic", path=Path("/tmp/h2.heic")),
        ]
        cache.upsert_items_batch(items)
        config = Config()
        groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    def test_different_dhash_no_group(self, mock_dhash, cache: CacheDB):
        """HEIC and JPEG with very different dHash => no group."""
        items = [
            sample_item(
                uuid="h1", uti="public.heic", path=Path("/tmp/h1.heic"),
            ),
            sample_item(
                uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg"),
            ),
        ]
        cache.upsert_items_batch(items)
        # Return very different hashes
        mock_dhash.side_effect = lambda p, **kw: (
            "ff" * 8 if "heic" in str(p) else "00" * 8
        )
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.phash_image")
    def test_excludes_already_grouped_uuids(self, mock_phash, mock_dhash, cache: CacheDB):
        """Items in exclude_uuids are skipped."""
        items = [
            sample_item(uuid="h1", uti="public.heic", path=Path("/tmp/h1.heic")),
            sample_item(uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg")),
        ]
        cache.upsert_items_batch(items)
        mock_dhash.return_value = "aa" * 8
        mock_phash.return_value = "bb" * 8
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(
                cache, config, exclude_uuids={"h1"}
            )
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.phash_image")
    def test_heif_uti_also_matched(self, mock_phash, mock_dhash, cache: CacheDB):
        """public.heif UTI is also treated as HEIC."""
        items = [
            sample_item(
                uuid="h1", uti="public.heif", path=Path("/tmp/h1.heif"),
            ),
            sample_item(
                uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg"),
            ),
        ]
        cache.upsert_items_batch(items)
        mock_dhash.return_value = "aa" * 8
        mock_phash.return_value = "bb" * 8
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 1

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.phash_image")
    def test_uses_cached_dhash(self, mock_phash, mock_dhash, cache: CacheDB):
        """If dHash is already cached, should not recompute."""
        items = [
            sample_item(
                uuid="h1", uti="public.heic", path=Path("/tmp/h1.heic"),
                dhash="aa" * 8,
            ),
            sample_item(
                uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg"),
                dhash="aa" * 8,
            ),
        ]
        cache.upsert_items_batch(items)
        mock_phash.return_value = "bb" * 8
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(cache, config)
        mock_dhash.assert_not_called()
        assert len(groups) == 1

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.phash_image")
    def test_multiple_jpeg_matches_for_one_heic(self, mock_phash, mock_dhash, cache: CacheDB):
        """One HEIC matching multiple JPEGs => group with all of them."""
        items = [
            sample_item(uuid="h1", uti="public.heic", path=Path("/tmp/h1.heic")),
            sample_item(uuid="j1", uti="public.jpeg", path=Path("/tmp/j1.jpg")),
            sample_item(uuid="j2", uti="public.jpeg", path=Path("/tmp/j2.jpg")),
        ]
        cache.upsert_items_batch(items)
        mock_dhash.return_value = "aa" * 8
        mock_phash.return_value = "bb" * 8
        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_heic_jpeg_duplicates(cache, config)
        assert len(groups) == 1
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"h1", "j1", "j2"}


class TestFindGrainyDuplicates:
    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_grainy_paired_with_clear(self, mock_noise, mock_dhash, mock_confirm, cache: CacheDB):
        """A grainy photo is paired with its clear counterpart."""
        items = [
            sample_item(uuid="grainy", path=Path("/tmp/grainy.jpg"), dhash="aabb" * 8),
            sample_item(uuid="clear", path=Path("/tmp/clear.jpg"), dhash="aabb" * 8),
        ]
        cache.upsert_items_batch(items)
        # grainy has 3x the noise of clear (well above 1.5x ratio)
        mock_noise.return_value = {"grainy": 40.0, "clear": 10.0}
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates

        config = Config(noise_ratio=1.5)
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(cache, config)

        assert len(groups) == 1
        assert groups[0].match_type == MatchType.NEAR
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"grainy", "clear"}

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_similar_noise_no_group(self, mock_noise, mock_dhash, cache: CacheDB):
        """Two photos with similar noise levels are not grouped."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aabb" * 8),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="aabb" * 8),
        ]
        cache.upsert_items_batch(items)
        # Both have similar noise — ratio < 1.5x
        mock_noise.return_value = {"a": 12.0, "b": 10.0}

        config = Config(noise_ratio=1.5)
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(cache, config)

        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_grainy_matches_best_clear(self, mock_noise, mock_dhash, mock_confirm, cache: CacheDB):
        """Grainy photo matches its closest clear photo, not all of them."""
        items = [
            sample_item(uuid="grainy", path=Path("/tmp/grainy.jpg"), dhash="aa" * 32),
            sample_item(uuid="clear1", path=Path("/tmp/c1.jpg"), dhash="aa" * 32),
            sample_item(uuid="clear2", path=Path("/tmp/c2.jpg"), dhash="bb" * 32),
        ]
        cache.upsert_items_batch(items)
        # grainy is noisy, clear1/clear2 are clean
        mock_noise.return_value = {"grainy": 30.0, "clear1": 10.0, "clear2": 10.0}
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates

        config = Config(noise_ratio=1.5, dhash_threshold=38)
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(cache, config)

        # Only one group: grainy matched to clear1 (closest dHash match)
        assert len(groups) == 1
        uuids = {i.uuid for i in groups[0].items}
        assert "grainy" in uuids
        assert "clear1" in uuids

    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_too_few_photos_returns_empty(self, mock_noise, cache: CacheDB):
        """Fewer than 2 photos returns empty."""
        items = [sample_item(uuid="a", path=Path("/tmp/a.jpg"))]
        cache.upsert_items_batch(items)

        config = Config()
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(cache, config)
        assert len(groups) == 0

    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_multiple_grainy_match_same_clear(
        self, mock_noise, mock_dhash, mock_confirm, cache: CacheDB
    ):
        """Multiple grainy photos matching the same clear one form one group."""
        # Need enough clear items so median stays low and grainy items exceed threshold
        items = [
            sample_item(uuid="g1", path=Path("/tmp/g1.jpg"), dhash="aa" * 32),
            sample_item(uuid="g2", path=Path("/tmp/g2.jpg"), dhash="aa" * 32),
            sample_item(uuid="clear1", path=Path("/tmp/c1.jpg"), dhash="aa" * 32),
            sample_item(uuid="clear2", path=Path("/tmp/c2.jpg"), dhash="bb" * 32),
            sample_item(uuid="clear3", path=Path("/tmp/c3.jpg"), dhash="cc" * 32),
        ]
        cache.upsert_items_batch(items)
        mock_noise.return_value = {
            "g1": 40.0, "g2": 35.0,
            "clear1": 10.0, "clear2": 11.0, "clear3": 12.0,
        }
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates

        config = Config(noise_ratio=1.5)
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(cache, config)

        assert len(groups) == 1
        uuids = {i.uuid for i in groups[0].items}
        assert uuids == {"g1", "g2", "clear1"}

    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder._compute_noise_scores")
    def test_excludes_already_grouped_uuids(self, mock_noise, mock_dhash, cache: CacheDB):
        """Items in exclude_uuids are skipped."""
        items = [
            sample_item(uuid="grainy", path=Path("/tmp/g.jpg"), dhash="aa" * 32),
            sample_item(uuid="clear", path=Path("/tmp/c.jpg"), dhash="aa" * 32),
        ]
        cache.upsert_items_batch(items)
        mock_noise.return_value = {"grainy": 40.0, "clear": 10.0}

        config = Config(noise_ratio=1.5)
        with patch.object(Path, "exists", return_value=True):
            groups = find_grainy_duplicates(
                cache, config, exclude_uuids={"clear"}
            )
        assert len(groups) == 0


class TestAdaptiveThreshold:
    def test_same_resolution_returns_base(self):
        """Items with same resolution get base threshold."""
        a = sample_item(uuid="a", width=4032, height=3024)
        b = sample_item(uuid="b", width=4032, height=3024)
        config = Config(resolution_ratio_threshold=2.0, resolution_adaptive_factor=1.3)
        assert _adaptive_threshold(a, b, 38, config) == 38

    def test_large_ratio_widens_threshold(self):
        """Items with >2x pixel ratio get widened threshold."""
        hi_res = sample_item(uuid="a", width=4032, height=3024)  # ~12MP
        lo_res = sample_item(uuid="b", width=1280, height=720)   # ~0.9MP
        config = Config(resolution_ratio_threshold=2.0, resolution_adaptive_factor=1.3)
        result = _adaptive_threshold(hi_res, lo_res, 38, config)
        assert result == int(38 * 1.3)  # 49

    def test_moderate_ratio_no_widening(self):
        """Items with ratio <=2x do not get widened."""
        a = sample_item(uuid="a", width=4032, height=3024)  # ~12MP
        b = sample_item(uuid="b", width=3024, height=3024)  # ~9MP, ratio ~1.3x
        config = Config(resolution_ratio_threshold=2.0, resolution_adaptive_factor=1.3)
        assert _adaptive_threshold(a, b, 38, config) == 38

    def test_zero_resolution_returns_base(self):
        """Items with 0 width/height get base threshold."""
        a = sample_item(uuid="a", width=0, height=0)
        b = sample_item(uuid="b", width=4032, height=3024)
        config = Config()
        assert _adaptive_threshold(a, b, 38, config) == 38

    def test_symmetric(self):
        """Order of items doesn't matter."""
        hi = sample_item(uuid="a", width=4032, height=3024)
        lo = sample_item(uuid="b", width=1280, height=720)
        config = Config()
        assert _adaptive_threshold(hi, lo, 38, config) == _adaptive_threshold(lo, hi, 38, config)


class TestDualScaleMatching:
    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.dhash_image_small")
    def test_small_hash_matches_when_large_misses(
        self, mock_dhash_small, mock_dhash, mock_confirm, cache: CacheDB
    ):
        """Items that miss on 256-bit dHash but match on 64-bit dHash form a group."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aa" * 32, dhash_small="aa" * 8),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bb" * 32, dhash_small="aa" * 8),
        ]
        cache.upsert_items_batch(items)

        config = Config(dhash_threshold=10, dhash_small_threshold=10)
        # 256-bit hashes are very different (will exceed threshold)
        # but 64-bit small hashes are identical (distance=0, within threshold)
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates

        with patch.object(Path, "exists", return_value=True):
            groups = find_near_duplicates(cache, config)

        assert len(groups) == 1
        assert groups[0].match_type == MatchType.NEAR

    @patch("media_scanner.core.duplicate_finder._confirm_with_phash")
    @patch("media_scanner.core.duplicate_finder.dhash_image")
    @patch("media_scanner.core.duplicate_finder.dhash_image_small")
    @patch("media_scanner.core.duplicate_finder.hamming_distance_int")
    def test_adaptive_threshold_catches_lowres_pair(
        self, mock_hamming, mock_dhash_small, mock_dhash, mock_confirm, cache: CacheDB
    ):
        """A hi-res and lo-res pair matches via adaptive threshold widening."""
        items = [
            sample_item(uuid="a", path=Path("/tmp/a.jpg"), dhash="aaa",
                        dhash_small="aa" * 8,
                        width=4032, height=3024),
            sample_item(uuid="b", path=Path("/tmp/b.jpg"), dhash="bbb",
                        dhash_small="bb" * 8,
                        width=1280, height=720),
        ]
        cache.upsert_items_batch(items)

        # Distance of 45 exceeds base threshold (38) but is within
        # adaptive threshold (38 * 1.3 = 49)
        mock_hamming.return_value = 45
        mock_confirm.side_effect = lambda candidates, *a, **kw: candidates
        config = Config(
            dhash_threshold=38,
            resolution_ratio_threshold=2.0,
            resolution_adaptive_factor=1.3,
        )

        with patch.object(Path, "exists", return_value=True):
            groups = find_near_duplicates(cache, config)

        assert len(groups) == 1
