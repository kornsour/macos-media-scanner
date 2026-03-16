"""Tests for video hashing via ffmpeg."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from media_scanner.core.video_hasher import (
    extract_keyframes,
    sha256_video,
    video_frames_similar,
)


class TestVideoFramesSimilar:
    @patch("media_scanner.core.hasher.hamming_distance")
    def test_matching_hashes(self, mock_hamming):
        """Frames with distances within threshold => similar."""
        mock_hamming.return_value = 5  # within threshold=10
        hashes_a = ["aa", "bb", "cc"]
        hashes_b = ["dd", "ee", "ff"]

        assert video_frames_similar(hashes_a, hashes_b, threshold=10) is True
        # Best-match alignment: each of 3 frames checks all 3 candidates = 9 calls
        assert mock_hamming.call_count == 9

    @patch("media_scanner.core.hasher.hamming_distance")
    def test_non_matching_hashes(self, mock_hamming):
        """Frames with distances above threshold => not similar."""
        mock_hamming.return_value = 50  # above threshold
        hashes_a = ["aa", "bb", "cc"]
        hashes_b = ["dd", "ee", "ff"]

        assert video_frames_similar(hashes_a, hashes_b, threshold=10) is False

    def test_empty_hashes_a(self):
        assert video_frames_similar([], ["aa", "bb"], threshold=10) is False

    def test_empty_hashes_b(self):
        assert video_frames_similar(["aa", "bb"], [], threshold=10) is False

    def test_both_empty(self):
        assert video_frames_similar([], [], threshold=10) is False

    @patch("media_scanner.core.hasher.hamming_distance")
    def test_different_lengths_uses_shorter(self, mock_hamming):
        """Uses the shorter list as the reference for matching."""
        mock_hamming.return_value = 3
        hashes_a = ["aa", "bb"]
        hashes_b = ["cc", "dd", "ee", "ff"]

        result = video_frames_similar(hashes_a, hashes_b, threshold=10)
        assert result is True
        # Best-match: each of 2 shorter frames checks all 4 candidates = 8 calls
        assert mock_hamming.call_count == 8

    @patch("media_scanner.core.hasher.hamming_distance")
    def test_60_percent_threshold(self, mock_hamming):
        """Needs >= 60% of frames matching to be similar."""
        # Best-match: for each of 5 frames, best distance across 5 candidates.
        # 3 frames find a match (<=10), 2 don't => 60% => True
        def _distances(a, b):
            # Frames a1,a2,a3 each find a close match; a4,a5 don't
            close = {("a1","b1"), ("a2","b2"), ("a3","b3")}
            return 5 if (a, b) in close else 50
        mock_hamming.side_effect = _distances
        hashes_a = ["a1", "a2", "a3", "a4", "a5"]
        hashes_b = ["b1", "b2", "b3", "b4", "b5"]

        assert video_frames_similar(hashes_a, hashes_b, threshold=10) is True

    @patch("media_scanner.core.hasher.hamming_distance")
    def test_below_60_percent_threshold(self, mock_hamming):
        """Less than 60% matching => not similar."""
        # Only 2 of 5 frames find a close match => 40% => False
        def _distances(a, b):
            close = {("a1","b1"), ("a2","b2")}
            return 5 if (a, b) in close else 50
        mock_hamming.side_effect = _distances
        hashes_a = ["a1", "a2", "a3", "a4", "a5"]
        hashes_b = ["b1", "b2", "b3", "b4", "b5"]

        assert video_frames_similar(hashes_a, hashes_b, threshold=10) is False


class TestExtractKeyframes:
    @patch("media_scanner.core.video_hasher.subprocess.run")
    def test_calls_ffmpeg(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(returncode=0)

        with patch("media_scanner.core.video_hasher.tempfile.mkdtemp", return_value=str(tmp_path)):
            result = extract_keyframes(Path("/fake/video.mp4"))

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "ffmpeg"
        assert "/fake/video.mp4" in args

    @patch("media_scanner.core.video_hasher.subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)
        result = extract_keyframes(Path("/fake/video.mp4"))
        assert result == []

    @patch("media_scanner.core.video_hasher.subprocess.run")
    def test_returns_empty_on_missing_ffmpeg(self, mock_run):
        mock_run.side_effect = FileNotFoundError("ffmpeg not found")
        result = extract_keyframes(Path("/fake/video.mp4"))
        assert result == []


class TestSha256Video:
    @patch("media_scanner.core.video_hasher.sha256_file")
    def test_delegates_to_sha256_file(self, mock_sha):
        mock_sha.return_value = "videohash"
        result = sha256_video(Path("/fake/video.mp4"))
        assert result == "videohash"
        mock_sha.assert_called_once_with(Path("/fake/video.mp4"))
