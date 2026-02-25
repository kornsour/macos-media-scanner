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
