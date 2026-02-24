"""File hashing - SHA-256 and perceptual hashes (dHash, pHash)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
import imagehash


def sha256_file(path: Path, chunk_size: int = 65536) -> str | None:
    """Compute SHA-256 hash of a file. Returns None if file is unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def dhash_image(path: Path, hash_size: int = 16) -> str | None:
    """Compute difference hash (dHash) for an image file.

    Returns hex string or None if the image can't be processed.
    """
    try:
        with Image.open(path) as img:
            h = imagehash.dhash(img, hash_size=hash_size)
            return str(h)
    except Exception:
        return None


def phash_image(path: Path, hash_size: int = 16) -> str | None:
    """Compute perceptual hash (pHash) for an image file.

    Returns hex string or None if the image can't be processed.
    """
    try:
        with Image.open(path) as img:
            h = imagehash.phash(img, hash_size=hash_size)
            return str(h)
    except Exception:
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute the Hamming distance between two hex hash strings."""
    h1 = imagehash.hex_to_hash(hash1)
    h2 = imagehash.hex_to_hash(hash2)
    return h1 - h2
