"""Parallel hashing utilities for the duplicate detection pipeline."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_scanner.data.cache import CacheDB

logger = logging.getLogger(__name__)


def compute_hashes_parallel(
    work_items: list[tuple[str, str]],
    hash_fn: Callable[..., str | None],
    hash_type: str,
    cache: CacheDB,
    max_workers: int,
    progress_callback: Callable[[int, int], None] | None = None,
    batch_size: int = 100,
) -> dict[str, str]:
    """Compute hashes in parallel and batch-update the cache.

    Args:
        work_items: List of (uuid, path_str) tuples.
        hash_fn: Callable that takes a Path and returns a hash string or None.
        hash_type: One of "sha256", "dhash", "phash".
        cache: CacheDB instance (only accessed from the main thread).
        max_workers: Number of parallel workers.
        progress_callback: Optional (done, total) callback.
        batch_size: How many results to accumulate before flushing to cache.

    Returns:
        Dict mapping uuid -> hash_value for successfully hashed items.
    """
    total = len(work_items)
    if total == 0:
        return {}

    results: dict[str, str] = {}
    pending_updates: list[tuple[str, str, str]] = []
    done_count = 0

    def _flush() -> None:
        if pending_updates:
            cache.update_hashes_batch(list(pending_updates))
            pending_updates.clear()

    if max_workers <= 1 or total <= 1:
        # Sequential fallback — no thread pool overhead
        for uuid, path_str in work_items:
            h = hash_fn(Path(path_str))
            if h:
                results[uuid] = h
                pending_updates.append((uuid, hash_type, h))
                if len(pending_updates) >= batch_size:
                    _flush()
            done_count += 1
            if progress_callback:
                progress_callback(done_count, total)
        _flush()
        return results

    # Parallel execution
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_uuid: dict[Future, str] = {}
        for uuid, path_str in work_items:
            future = executor.submit(hash_fn, Path(path_str))
            future_to_uuid[future] = uuid

        for future in as_completed(future_to_uuid):
            uuid = future_to_uuid[future]
            done_count += 1
            try:
                h = future.result()
            except Exception:
                logger.debug("Hash computation failed for %s", uuid, exc_info=True)
                h = None

            if h:
                results[uuid] = h
                pending_updates.append((uuid, hash_type, h))
                if len(pending_updates) >= batch_size:
                    _flush()

            if progress_callback:
                progress_callback(done_count, total)

    _flush()
    return results


def compute_video_hashes_parallel(
    work_items: list[tuple[str, str]],
    max_workers: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, list[str]]:
    """Compute video keyframe hashes in parallel.

    Uses max(1, max_workers // 2) threads since each ffmpeg call is CPU-heavy.

    Returns:
        Dict mapping uuid -> list of dhash hex strings.
    """
    from media_scanner.core.video_hasher import dhash_video

    total = len(work_items)
    if total == 0:
        return {}

    results: dict[str, list[str]] = {}
    done_count = 0
    video_workers = max(1, max_workers // 2)

    if video_workers <= 1 or total <= 1:
        for uuid, path_str in work_items:
            fh = dhash_video(Path(path_str))
            if fh:
                results[uuid] = fh
            done_count += 1
            if progress_callback:
                progress_callback(done_count, total)
        return results

    with ThreadPoolExecutor(max_workers=video_workers) as executor:
        future_to_uuid: dict[Future, str] = {}
        for uuid, path_str in work_items:
            future = executor.submit(dhash_video, Path(path_str))
            future_to_uuid[future] = uuid

        for future in as_completed(future_to_uuid):
            uuid = future_to_uuid[future]
            done_count += 1
            try:
                fh = future.result()
            except Exception:
                logger.debug("Video hash failed for %s", uuid, exc_info=True)
                fh = None
            if fh:
                results[uuid] = fh
            if progress_callback:
                progress_callback(done_count, total)

    return results
