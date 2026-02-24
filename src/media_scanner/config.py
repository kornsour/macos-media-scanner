"""Configuration management for media-scanner."""

from __future__ import annotations

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

    # Duplicate detection thresholds
    dhash_threshold: int = 10  # hamming distance for dHash near-match
    phash_threshold: int = 12  # hamming distance for pHash confirmation
    video_duration_tolerance: float = 2.0  # seconds

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
