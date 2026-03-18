"""Tests for file and perceptual hashing."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from media_scanner.core.hasher import (
    dhash_image,
    dhash_image_small,
    hamming_distance,
    noise_level,
    phash_image,
    phash_image_small,
    sha256_file,
)


class TestSha256File:
    def test_known_content(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        result = sha256_file(f)
        assert result is not None
        # Known SHA-256 of "hello world"
        assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = sha256_file(f)
        assert result is not None
        # SHA-256 of empty string
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_missing_file_returns_none(self, tmp_path: Path):
        result = sha256_file(tmp_path / "nonexistent.bin")
        assert result is None

    def test_large_file(self, tmp_path: Path):
        f = tmp_path / "large.bin"
        f.write_bytes(b"x" * 200_000)
        result = sha256_file(f)
        assert result is not None
        assert len(result) == 64  # hex digest length

    def test_deterministic(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"deterministic")
        assert sha256_file(f) == sha256_file(f)


class TestHammingDistance:
    def test_identical_hashes(self):
        h = "ff00ff00ff00ff00"
        assert hamming_distance(h, h) == 0

    def test_completely_different(self):
        # All zeros vs all ones (for a short hash)
        h1 = "0000"
        h2 = "ffff"
        dist = hamming_distance(h1, h2)
        assert dist == 16  # 16 bits different

    def test_one_bit_different(self):
        h1 = "0000"
        h2 = "0001"
        dist = hamming_distance(h1, h2)
        assert dist == 1

    def test_symmetry(self):
        h1 = "abcd"
        h2 = "abce"
        assert hamming_distance(h1, h2) == hamming_distance(h2, h1)


class TestDhashImage:
    @patch("media_scanner.core.hasher.Image")
    @patch("media_scanner.core.hasher.imagehash")
    def test_returns_hex_string(self, mock_imagehash, mock_image):
        mock_img = MagicMock()
        mock_image.open.return_value.__enter__ = MagicMock(return_value=mock_img)
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)
        mock_imagehash.dhash.return_value = MagicMock(__str__=lambda self: "abcdef1234567890")

        result = dhash_image(Path("/fake/image.jpg"))
        assert result == "abcdef1234567890"
        mock_imagehash.dhash.assert_called_once()

    @patch("media_scanner.core.hasher.Image")
    def test_returns_none_on_error(self, mock_image):
        mock_image.open.side_effect = Exception("corrupt image")
        result = dhash_image(Path("/fake/bad.jpg"))
        assert result is None


class TestPhashImage:
    @patch("media_scanner.core.hasher.Image")
    @patch("media_scanner.core.hasher.imagehash")
    def test_returns_hex_string(self, mock_imagehash, mock_image):
        mock_img = MagicMock()
        mock_image.open.return_value.__enter__ = MagicMock(return_value=mock_img)
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)
        mock_imagehash.phash.return_value = MagicMock(__str__=lambda self: "1234567890abcdef")

        result = phash_image(Path("/fake/image.jpg"))
        assert result == "1234567890abcdef"
        mock_imagehash.phash.assert_called_once()

    @patch("media_scanner.core.hasher.Image")
    def test_returns_none_on_error(self, mock_image):
        mock_image.open.side_effect = Exception("corrupt")
        result = phash_image(Path("/fake/bad.jpg"))
        assert result is None


class TestDhashImageSmall:
    @patch("media_scanner.core.hasher.Image")
    @patch("media_scanner.core.hasher.imagehash")
    def test_calls_dhash_with_hash_size_8(self, mock_imagehash, mock_image):
        mock_img = MagicMock()
        mock_image.open.return_value.__enter__ = MagicMock(return_value=mock_img)
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)
        mock_imagehash.dhash.return_value = MagicMock(__str__=lambda self: "abcdef12")

        result = dhash_image_small(Path("/fake/image.jpg"))
        assert result == "abcdef12"
        mock_imagehash.dhash.assert_called_once_with(mock_img, hash_size=8)

    @patch("media_scanner.core.hasher.Image")
    def test_returns_none_on_error(self, mock_image):
        mock_image.open.side_effect = Exception("corrupt")
        assert dhash_image_small(Path("/fake/bad.jpg")) is None


class TestPhashImageSmall:
    @patch("media_scanner.core.hasher.Image")
    @patch("media_scanner.core.hasher.imagehash")
    def test_calls_phash_with_hash_size_8(self, mock_imagehash, mock_image):
        mock_img = MagicMock()
        mock_image.open.return_value.__enter__ = MagicMock(return_value=mock_img)
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)
        mock_imagehash.phash.return_value = MagicMock(__str__=lambda self: "12345678")

        result = phash_image_small(Path("/fake/image.jpg"))
        assert result == "12345678"
        mock_imagehash.phash.assert_called_once_with(mock_img, hash_size=8)

    @patch("media_scanner.core.hasher.Image")
    def test_returns_none_on_error(self, mock_image):
        mock_image.open.side_effect = Exception("corrupt")
        assert phash_image_small(Path("/fake/bad.jpg")) is None


class TestNoiseLevel:
    def test_returns_float_for_valid_image(self, tmp_path: Path):
        """Creates a simple test image and verifies noise_level returns a float."""
        from PIL import Image
        img = Image.new("L", (100, 100), color=128)
        f = tmp_path / "test.png"
        img.save(f)
        result = noise_level(f)
        assert result is not None
        assert isinstance(result, float)
        assert result >= 0

    def test_noisy_image_scores_higher(self, tmp_path: Path):
        """A noisy image should have a higher noise level than a uniform one."""
        import numpy as np
        from PIL import Image

        # Uniform gray image
        uniform = Image.new("L", (512, 512), color=128)
        f_uniform = tmp_path / "uniform.png"
        uniform.save(f_uniform)

        # Noisy image
        rng = np.random.default_rng(42)
        noisy_arr = rng.integers(0, 255, size=(512, 512), dtype=np.uint8)
        noisy = Image.fromarray(noisy_arr, mode="L")
        f_noisy = tmp_path / "noisy.png"
        noisy.save(f_noisy)

        uniform_score = noise_level(f_uniform)
        noisy_score = noise_level(f_noisy)

        assert uniform_score is not None
        assert noisy_score is not None
        assert noisy_score > uniform_score

    def test_missing_file_returns_none(self):
        result = noise_level(Path("/fake/missing.jpg"))
        assert result is None
