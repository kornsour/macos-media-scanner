"""Formatting helpers for sizes, dates, etc."""

from __future__ import annotations

from datetime import datetime


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def format_date(dt: datetime | None) -> str:
    """Format a datetime for display."""
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_resolution(width: int, height: int) -> str:
    """Format resolution for display."""
    if width == 0 and height == 0:
        return "—"
    return f"{width}x{height}"


def format_duration(seconds: float | None) -> str:
    """Format video duration."""
    if seconds is None:
        return "—"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def format_count(n: int) -> str:
    """Format a count with comma separators."""
    return f"{n:,}"
