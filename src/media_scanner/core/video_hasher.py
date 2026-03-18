"""Video hashing via ffmpeg keyframe extraction."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from media_scanner.core.hasher import dhash_image, sha256_file

logger = logging.getLogger(__name__)


def _cleanup_frames(frames: list[Path]) -> None:
    """Remove temporary frame files and their parent directory."""
    if not frames:
        return
    try:
        shutil.rmtree(frames[0].parent, ignore_errors=True)
    except Exception:
        pass


def extract_keyframes(video_path: Path, max_frames: int = 8) -> list[Path]:
    """Extract keyframes from a video using ffmpeg.

    Returns paths to temporary frame images.
    """
    tmp_dir = tempfile.mkdtemp(prefix="media-scanner-frames-")
    output_pattern = str(Path(tmp_dir) / "frame_%04d.jpg")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(video_path),
                "-vf", f"select='eq(pict_type,I)',scale=320:-1",
                "-vsync", "vfr",
                "-frames:v", str(max_frames),
                "-q:v", "2",
                output_pattern,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug(
                "ffmpeg failed (exit %d) for %s: %s",
                result.returncode, video_path.name,
                result.stderr[:200] if result.stderr else "",
            )
    except subprocess.TimeoutExpired:
        logger.debug("ffmpeg timed out for %s", video_path.name)
        return []
    except FileNotFoundError:
        logger.debug("ffmpeg not found")
        return []

    frames = sorted(Path(tmp_dir).glob("frame_*.jpg"))
    logger.debug("Extracted %d keyframe(s) from %s", len(frames), video_path.name)
    return frames


def sha256_video(path: Path) -> str | None:
    """SHA-256 of the entire video file."""
    return sha256_file(path)


def dhash_video(video_path: Path, max_frames: int = 8) -> list[str]:
    """Compute dHash for keyframes of a video.

    Returns a list of dhash hex strings, one per keyframe.
    """
    frames = extract_keyframes(video_path, max_frames=max_frames)
    try:
        hashes = []
        for frame_path in frames:
            h = dhash_image(frame_path)
            if h:
                hashes.append(h)
        return hashes
    finally:
        _cleanup_frames(frames)


def extract_sampled_frames(
    video_path: Path, num_frames: int = 5
) -> list[Path]:
    """Extract frames evenly spaced across a video's duration.

    Unlike extract_keyframes (which grabs I-frames from the start),
    this samples at regular time intervals to cover the full video.
    Uses a single ffmpeg call with a select filter for performance.
    Returns paths to temporary frame images.
    """
    tmp_dir = tempfile.mkdtemp(prefix="media-scanner-motion-")

    # Get duration via ffprobe
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(video_path),
            ],
            capture_output=True,
            timeout=10,
        )
        if probe.returncode != 0:
            return []
        import json as _json
        duration = float(_json.loads(probe.stdout)["format"]["duration"])
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, KeyError):
        return []

    if duration <= 0:
        return []

    # Build timestamps for evenly spaced samples
    interval = duration / (num_frames + 1)
    timestamps = [interval * i for i in range(1, num_frames + 1)]

    # Build a select filter that picks the frame closest to each timestamp
    # e.g. "lt(prev_pts*TB,0.5)*gte(pts*TB,0.5)+lt(prev_pts*TB,1.0)*gte(pts*TB,1.0)+..."
    # Simpler approach: use between() windows around each timestamp
    select_parts = [f"lt(prev_pts*TB\\,{ts:.3f})*gte(pts*TB\\,{ts:.3f})" for ts in timestamps]
    select_expr = "+".join(select_parts)

    output_pattern = str(Path(tmp_dir) / "sample_%04d.jpg")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(video_path),
                "-vf", f"select='{select_expr}',scale=160:-1",
                "-vsync", "vfr",
                "-frames:v", str(num_frames),
                "-q:v", "4",
                output_pattern,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug(
                "ffmpeg sampled frames failed (exit %d) for %s",
                result.returncode, video_path.name,
            )
    except subprocess.TimeoutExpired:
        logger.debug("ffmpeg sampled frames timed out for %s", video_path.name)
        return []
    except FileNotFoundError:
        logger.debug("ffmpeg not found")
        return []

    frames = sorted(Path(tmp_dir).glob("sample_*.jpg"))
    return frames


def motion_score(video_path: Path, num_frames: int = 5) -> float:
    """Compute a motion score for a video (0.0 to 1.0).

    Samples frames evenly across the video and compares consecutive
    pairs using dHash.  A frozen/corrupted video will have nearly
    identical frames throughout, yielding a low score.

    Returns the fraction of consecutive frame pairs that show motion
    (hamming distance > 3).  1.0 = full motion, 0.0 = completely frozen.
    """
    from media_scanner.core.hasher import hamming_distance

    frames = extract_sampled_frames(video_path, num_frames=num_frames)
    if len(frames) < 2:
        _cleanup_frames(frames)
        return 1.0

    try:
        hashes = []
        for frame_path in frames:
            h = dhash_image(frame_path)
            if h:
                hashes.append(h)

        if len(hashes) < 2:
            return 1.0

        # Count pairs with actual motion (hamming distance > 3)
        motion_pairs = 0
        total_pairs = len(hashes) - 1
        for i in range(total_pairs):
            if hamming_distance(hashes[i], hashes[i + 1]) > 3:
                motion_pairs += 1

        return motion_pairs / total_pairs
    finally:
        _cleanup_frames(frames)


def video_frames_similar(
    hashes_a: list[str], hashes_b: list[str], threshold: int = 10
) -> bool:
    """Check if two sets of video keyframe hashes are similar.

    Uses best-match alignment: for each frame in the shorter list, finds the
    closest match in the longer list.  This handles slight start-offset
    differences between re-encoded copies of the same video.

    Returns True if the majority of frames find a match within threshold.
    """
    if not hashes_a or not hashes_b:
        return False

    from media_scanner.core.hasher import hamming_distance

    # Ensure hashes_a is the shorter list
    if len(hashes_a) > len(hashes_b):
        hashes_a, hashes_b = hashes_b, hashes_a

    matches = 0
    for ha in hashes_a:
        # Find best match in hashes_b for this frame
        best = min(hamming_distance(ha, hb) for hb in hashes_b)
        if best <= threshold:
            matches += 1

    return matches >= len(hashes_a) * 0.6
