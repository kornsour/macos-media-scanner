"""Video hashing via ffmpeg keyframe extraction."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from media_scanner.core.hasher import dhash_image, sha256_file

logger = logging.getLogger(__name__)


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
    hashes = []
    for frame_path in frames:
        h = dhash_image(frame_path)
        if h:
            hashes.append(h)
    # Clean up temp files
    for frame_path in frames:
        try:
            frame_path.unlink()
        except OSError:
            pass
    if frames:
        try:
            frames[0].parent.rmdir()
        except OSError:
            pass
    return hashes


def extract_sampled_frames(
    video_path: Path, num_frames: int = 10
) -> list[Path]:
    """Extract frames evenly spaced across a video's duration.

    Unlike extract_keyframes (which grabs I-frames from the start),
    this samples at regular time intervals to cover the full video.
    Returns paths to temporary frame images.
    """
    tmp_dir = tempfile.mkdtemp(prefix="media-scanner-motion-")
    frames: list[Path] = []

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

    # Sample at evenly spaced timestamps
    interval = duration / (num_frames + 1)
    for i in range(1, num_frames + 1):
        ts = interval * i
        out_path = Path(tmp_dir) / f"sample_{i:04d}.jpg"
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-ss", f"{ts:.3f}",
                    "-i", str(video_path),
                    "-vframes", "1",
                    "-vf", "scale=160:-1",
                    "-q:v", "4",
                    str(out_path),
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0 and out_path.exists():
                frames.append(out_path)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return frames


def motion_score(video_path: Path, num_frames: int = 10) -> float:
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
        # Can't assess — assume OK
        return 1.0

    hashes = []
    for frame_path in frames:
        h = dhash_image(frame_path)
        if h:
            hashes.append(h)

    # Clean up temp files
    for frame_path in frames:
        try:
            frame_path.unlink()
        except OSError:
            pass
    if frames:
        try:
            frames[0].parent.rmdir()
        except OSError:
            pass

    if len(hashes) < 2:
        return 1.0

    # Count pairs with actual motion (hamming distance > 3)
    motion_pairs = 0
    total_pairs = len(hashes) - 1
    for i in range(total_pairs):
        if hamming_distance(hashes[i], hashes[i + 1]) > 3:
            motion_pairs += 1

    return motion_pairs / total_pairs


def video_frames_similar(
    hashes_a: list[str], hashes_b: list[str], threshold: int = 10
) -> bool:
    """Check if two sets of video keyframe hashes are similar.

    Returns True if the majority of matched frames are within threshold.
    """
    if not hashes_a or not hashes_b:
        return False

    from media_scanner.core.hasher import hamming_distance

    matches = 0
    compared = min(len(hashes_a), len(hashes_b))
    for ha, hb in zip(hashes_a, hashes_b):
        if hamming_distance(ha, hb) <= threshold:
            matches += 1

    return matches >= compared * 0.6
