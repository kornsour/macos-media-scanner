"""Configuration management for media-scanner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_BASE_DIR = Path.home() / ".media-scanner"
DEFAULT_DB_PATH = DEFAULT_BASE_DIR / "cache.db"


@dataclass
class Config:
    """Runtime configuration."""

    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    photos_library: Path | None = None  # None = system default
    verbose: bool = False

    # Duplicate detection thresholds (calibrated for hash_size=16 → 256-bit hashes)
    dhash_threshold: int = 38  # hamming distance for dHash near-match (~15% of 256 bits)
    phash_threshold: int = 46  # hamming distance for pHash confirmation (~18% of 256 bits)
    video_duration_tolerance: float = 2.0  # seconds
    noise_ratio: float = 1.5  # min grainy/clear noise ratio for grainy duplicate detection

    # Dual-scale small hash thresholds (hash_size=8 → 64-bit hashes)
    dhash_small_threshold: int = 10  # ~15% of 64 bits
    phash_small_threshold: int = 12  # ~18% of 64 bits

    # Resolution-adaptive threshold scaling
    resolution_ratio_threshold: float = 2.0  # pixel-count ratio to trigger adaptive widening
    resolution_adaptive_factor: float = 1.3  # multiply threshold by this when ratio exceeded

    # Corruption detection
    corrupt_motion_threshold: float = 0.25  # videos with motion_score <= this are flagged

    # Parallelism
    max_workers: int = field(default_factory=lambda: min(os.cpu_count() or 4, 4))

    # Quality scoring weights
    quality_weights: dict[str, float] = field(default_factory=lambda: {
        "resolution": 0.30,
        "format": 0.20,
        "file_size": 0.15,
        "metadata": 0.10,
        "date_originality": 0.10,
        "apple_score": 0.10,
        "edit_status": 0.05,
    })

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
