"""Metadata analysis: stats, missing metadata, big files, timeline."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MediaItem


@dataclass
class TimelinePeriod:
    start: datetime
    end: datetime
    count: int
    label: str  # e.g. "2024-03" for monthly


@dataclass
class MissingMetaReport:
    no_date: list[MediaItem]
    no_gps: list[MediaItem]
    no_faces: list[MediaItem]
    no_keywords: list[MediaItem]


def get_missing_metadata(cache: CacheDB) -> MissingMetaReport:
    """Find items missing key metadata."""
    items = cache.get_all_items()
    return MissingMetaReport(
        no_date=[i for i in items if i.date_created is None],
        no_gps=[i for i in items if not i.has_gps],
        no_faces=[i for i in items if not i.persons],
        no_keywords=[i for i in items if not i.keywords],
    )


def get_biggest_files(cache: CacheDB, limit: int = 50) -> list[MediaItem]:
    """Return the largest files in the library."""
    items = cache.get_all_items()
    items.sort(key=lambda i: i.file_size, reverse=True)
    return items[:limit]


def get_timeline(cache: CacheDB, granularity: str = "month") -> list[TimelinePeriod]:
    """Build a timeline of media density by month or year."""
    items = cache.get_all_items()
    dated = [i for i in items if i.date_created]
    if not dated:
        return []

    counter: Counter[str] = Counter()
    for item in dated:
        if granularity == "year":
            key = item.date_created.strftime("%Y")
        else:
            key = item.date_created.strftime("%Y-%m")
        counter[key] += 1

    periods = []
    for key in sorted(counter.keys()):
        if granularity == "year":
            start = datetime.strptime(key, "%Y")
            end = datetime(start.year, 12, 31, 23, 59, 59)
        else:
            start = datetime.strptime(key, "%Y-%m")
            # End of month approximation
            if start.month == 12:
                end = datetime(start.year + 1, 1, 1)
            else:
                end = datetime(start.year, start.month + 1, 1)
        periods.append(TimelinePeriod(
            start=start,
            end=end,
            count=counter[key],
            label=key,
        ))

    return periods


def find_timeline_gaps(
    periods: list[TimelinePeriod], min_gap_months: int = 3
) -> list[tuple[str, str]]:
    """Find gaps in the timeline where no photos were taken."""
    gaps = []
    for i in range(len(periods) - 1):
        current = periods[i]
        next_p = periods[i + 1]
        # Simple month-based gap detection
        c_year, c_month = (
            int(current.label.split("-")[0]),
            int(current.label.split("-")[1]) if "-" in current.label else 1,
        )
        n_year, n_month = (
            int(next_p.label.split("-")[0]),
            int(next_p.label.split("-")[1]) if "-" in next_p.label else 1,
        )
        months_apart = (n_year - c_year) * 12 + (n_month - c_month)
        if months_apart >= min_gap_months:
            gaps.append((current.label, next_p.label))
    return gaps


def get_album_distribution(cache: CacheDB) -> dict[str, int]:
    """Count items per album."""
    items = cache.get_all_items()
    counter: Counter[str] = Counter()
    for item in items:
        for album in item.albums:
            counter[album] += 1
    return dict(counter.most_common())


def get_person_distribution(cache: CacheDB) -> dict[str, int]:
    """Count items per person."""
    items = cache.get_all_items()
    counter: Counter[str] = Counter()
    for item in items:
        for person in item.persons:
            counter[person] += 1
    return dict(counter.most_common())
