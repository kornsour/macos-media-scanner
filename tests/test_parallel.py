"""Tests for parallel hashing utilities."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from media_scanner.core.parallel import (
    compute_hashes_parallel,
    compute_video_hashes_parallel,
)


class TestComputeHashesParallel:
    def test_sequential_fallback_single_worker(self):
        """With max_workers=1, runs sequentially and produces correct results."""
        mock_cache = MagicMock()
        work = [("u1", "/tmp/a.jpg"), ("u2", "/tmp/b.jpg")]
        results = compute_hashes_parallel(
            work_items=work,
            hash_fn=lambda p: f"hash-{p.name}",
            hash_type="sha256",
            cache=mock_cache,
            max_workers=1,
        )
        assert results == {"u1": "hash-a.jpg", "u2": "hash-b.jpg"}
        mock_cache.update_hashes_batch.assert_called()

    def test_parallel_execution(self):
        """With max_workers>1, produces correct results."""
        mock_cache = MagicMock()
        work = [("u1", "/tmp/a.jpg"), ("u2", "/tmp/b.jpg"), ("u3", "/tmp/c.jpg")]
        results = compute_hashes_parallel(
            work_items=work,
            hash_fn=lambda p: f"hash-{p.name}",
            hash_type="sha256",
            cache=mock_cache,
            max_workers=3,
        )
        assert len(results) == 3
        assert results["u1"] == "hash-a.jpg"
        assert results["u2"] == "hash-b.jpg"
        assert results["u3"] == "hash-c.jpg"

    def test_hash_failure_skipped(self):
        """Items where hash_fn returns None are excluded from results."""
        mock_cache = MagicMock()
        work = [("u1", "/tmp/a.jpg"), ("u2", "/tmp/b.jpg")]
        results = compute_hashes_parallel(
            work_items=work,
            hash_fn=lambda p: None,
            hash_type="sha256",
            cache=mock_cache,
            max_workers=2,
        )
        assert results == {}

    def test_progress_callback_called(self):
        """Progress callback fires for each item."""
        mock_cache = MagicMock()
        callback = MagicMock()
        work = [("u1", "/tmp/a.jpg"), ("u2", "/tmp/b.jpg")]
        compute_hashes_parallel(
            work_items=work,
            hash_fn=lambda p: "h",
            hash_type="sha256",
            cache=mock_cache,
            max_workers=2,
            progress_callback=callback,
        )
        assert callback.call_count == 2

    def test_empty_work_items(self):
        """No work items => empty results, no cache interaction."""
        mock_cache = MagicMock()
        results = compute_hashes_parallel(
            work_items=[],
            hash_fn=lambda p: "h",
            hash_type="sha256",
            cache=mock_cache,
            max_workers=4,
        )
        assert results == {}
        mock_cache.update_hashes_batch.assert_not_called()

    def test_exception_in_hash_fn_handled(self):
        """If hash_fn raises, that item is skipped, others still processed."""
        mock_cache = MagicMock()

        def flaky_hash(p):
            if "bad" in str(p):
                raise RuntimeError("boom")
            return "good"

        work = [("u1", "/tmp/good.jpg"), ("u2", "/tmp/bad.jpg")]
        results = compute_hashes_parallel(
            work_items=work,
            hash_fn=flaky_hash,
            hash_type="sha256",
            cache=mock_cache,
            max_workers=2,
        )
        assert "u1" in results
        assert "u2" not in results

    def test_batch_flush(self):
        """Cache is flushed in batches when batch_size is exceeded."""
        mock_cache = MagicMock()
        work = [(f"u{i}", f"/tmp/{i}.jpg") for i in range(5)]
        compute_hashes_parallel(
            work_items=work,
            hash_fn=lambda p: "h",
            hash_type="sha256",
            cache=mock_cache,
            max_workers=1,
            batch_size=2,
        )
        # 5 items with batch_size=2: flushes at 2, 4, and final flush with 1
        assert mock_cache.update_hashes_batch.call_count == 3


class TestComputeVideoHashesParallel:
    def test_sequential_fallback(self):
        """With max_workers=1, runs sequentially."""
        with patch("media_scanner.core.video_hasher.dhash_video") as mock_dhash:
            mock_dhash.return_value = ["aa", "bb"]
            work = [("u1", "/tmp/v1.mov"), ("u2", "/tmp/v2.mov")]
            results = compute_video_hashes_parallel(
                work_items=work,
                max_workers=1,
            )
        assert len(results) == 2
        assert results["u1"] == ["aa", "bb"]

    def test_parallel_execution(self):
        """With max_workers>1, produces correct results."""
        with patch("media_scanner.core.video_hasher.dhash_video") as mock_dhash:
            mock_dhash.return_value = ["cc"]
            work = [("u1", "/tmp/v1.mov"), ("u2", "/tmp/v2.mov")]
            results = compute_video_hashes_parallel(
                work_items=work,
                max_workers=4,
            )
        assert len(results) == 2

    def test_empty_result_skipped(self):
        """Items where dhash_video returns empty list are excluded."""
        with patch("media_scanner.core.video_hasher.dhash_video") as mock_dhash:
            mock_dhash.return_value = []
            work = [("u1", "/tmp/v1.mov")]
            results = compute_video_hashes_parallel(
                work_items=work,
                max_workers=2,
            )
        assert results == {}

    def test_empty_work_items(self):
        """No work items => empty results."""
        results = compute_video_hashes_parallel(
            work_items=[],
            max_workers=4,
        )
        assert results == {}

    def test_progress_callback(self):
        """Progress callback fires for each item."""
        with patch("media_scanner.core.video_hasher.dhash_video") as mock_dhash:
            mock_dhash.return_value = ["aa"]
            callback = MagicMock()
            work = [("u1", "/tmp/v1.mov"), ("u2", "/tmp/v2.mov")]
            compute_video_hashes_parallel(
                work_items=work,
                max_workers=1,
                progress_callback=callback,
            )
        assert callback.call_count == 2

    def test_works_with_high_workers(self):
        """Video hashing still works correctly with high max_workers."""
        with patch("media_scanner.core.video_hasher.dhash_video") as mock_dhash:
            mock_dhash.return_value = ["aa", "bb"]
            work = [("u1", "/tmp/v1.mov"), ("u2", "/tmp/v2.mov")]
            results = compute_video_hashes_parallel(
                work_items=work,
                max_workers=8,
            )
        assert len(results) == 2
        assert results["u1"] == ["aa", "bb"]
