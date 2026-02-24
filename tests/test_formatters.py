"""Tests for UI formatting helpers."""

from datetime import datetime

from media_scanner.ui.formatters import (
    format_count,
    format_date,
    format_duration,
    format_resolution,
    format_size,
)


class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.0 KB"
        assert format_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_size(1024 ** 2) == "1.0 MB"
        assert format_size(int(2.5 * 1024 ** 2)) == "2.5 MB"

    def test_gigabytes(self):
        assert format_size(1024 ** 3) == "1.00 GB"
        assert format_size(int(1.5 * 1024 ** 3)) == "1.50 GB"


class TestFormatDate:
    def test_none_returns_dash(self):
        assert format_date(None) == "—"

    def test_valid_datetime(self):
        dt = datetime(2024, 6, 15, 10, 30)
        assert format_date(dt) == "2024-06-15 10:30"

    def test_midnight(self):
        dt = datetime(2024, 1, 1, 0, 0)
        assert format_date(dt) == "2024-01-01 00:00"


class TestFormatResolution:
    def test_zero_returns_dash(self):
        assert format_resolution(0, 0) == "—"

    def test_normal_resolution(self):
        assert format_resolution(4032, 3024) == "4032x3024"

    def test_1080p(self):
        assert format_resolution(1920, 1080) == "1920x1080"


class TestFormatDuration:
    def test_none_returns_dash(self):
        assert format_duration(None) == "—"

    def test_seconds_only(self):
        assert format_duration(45.0) == "0:45"

    def test_minutes_and_seconds(self):
        assert format_duration(125.0) == "2:05"

    def test_hours(self):
        assert format_duration(3661.0) == "1:01:01"

    def test_zero(self):
        assert format_duration(0.0) == "0:00"


class TestFormatCount:
    def test_small_numbers(self):
        assert format_count(0) == "0"
        assert format_count(999) == "999"

    def test_comma_separators(self):
        assert format_count(1000) == "1,000"
        assert format_count(1_000_000) == "1,000,000"
        assert format_count(123456) == "123,456"
