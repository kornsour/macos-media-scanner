"""Tests for metadata analysis functions."""

from datetime import datetime

from media_scanner.core.metadata_analyzer import (
    find_timeline_gaps,
    get_album_distribution,
    get_biggest_files,
    get_missing_metadata,
    get_person_distribution,
    get_timeline,
    TimelinePeriod,
)
from media_scanner.data.cache import CacheDB
from tests.conftest import sample_item


class TestGetMissingMetadata:
    def test_identifies_missing_dates(self, populated_cache: CacheDB):
        report = get_missing_metadata(populated_cache)
        no_date_uuids = [i.uuid for i in report.no_date]
        assert "photo-no-date" in no_date_uuids

    def test_identifies_missing_gps(self, populated_cache: CacheDB):
        report = get_missing_metadata(populated_cache)
        no_gps_uuids = [i.uuid for i in report.no_gps]
        assert "photo-2" in no_gps_uuids
        assert "photo-no-date" in no_gps_uuids

    def test_identifies_missing_faces(self, populated_cache: CacheDB):
        report = get_missing_metadata(populated_cache)
        no_faces_uuids = [i.uuid for i in report.no_faces]
        assert "photo-2" in no_faces_uuids
        # Items with persons should not be listed
        assert "photo-1" not in no_faces_uuids

    def test_identifies_missing_keywords(self, populated_cache: CacheDB):
        report = get_missing_metadata(populated_cache)
        no_kw_uuids = [i.uuid for i in report.no_keywords]
        assert "photo-2" in no_kw_uuids
        assert "photo-1" not in no_kw_uuids

    def test_empty_db(self, cache: CacheDB):
        report = get_missing_metadata(cache)
        assert report.no_date == []
        assert report.no_gps == []
        assert report.no_faces == []
        assert report.no_keywords == []


class TestGetBiggestFiles:
    def test_sorted_descending(self, populated_cache: CacheDB):
        biggest = get_biggest_files(populated_cache)
        sizes = [i.file_size for i in biggest]
        assert sizes == sorted(sizes, reverse=True)

    def test_limit(self, populated_cache: CacheDB):
        biggest = get_biggest_files(populated_cache, limit=3)
        assert len(biggest) == 3

    def test_default_limit(self, populated_cache: CacheDB):
        biggest = get_biggest_files(populated_cache)
        # populated_cache has 8 items, default limit is 50
        assert len(biggest) == 8

    def test_empty_db(self, cache: CacheDB):
        assert get_biggest_files(cache) == []


class TestGetTimeline:
    def test_monthly_granularity(self, populated_cache: CacheDB):
        periods = get_timeline(populated_cache, granularity="month")
        assert len(periods) > 0
        assert all(isinstance(p, TimelinePeriod) for p in periods)
        # Labels should be YYYY-MM format
        for p in periods:
            assert len(p.label.split("-")) == 2

    def test_yearly_granularity(self, populated_cache: CacheDB):
        periods = get_timeline(populated_cache, granularity="year")
        assert len(periods) >= 1
        for p in periods:
            assert len(p.label) == 4  # YYYY

    def test_empty_db(self, cache: CacheDB):
        assert get_timeline(cache) == []

    def test_periods_sorted(self, populated_cache: CacheDB):
        periods = get_timeline(populated_cache)
        labels = [p.label for p in periods]
        assert labels == sorted(labels)

    def test_counts_are_positive(self, populated_cache: CacheDB):
        periods = get_timeline(populated_cache)
        for p in periods:
            assert p.count > 0

    def test_excludes_items_without_dates(self, populated_cache: CacheDB):
        periods = get_timeline(populated_cache)
        total_counted = sum(p.count for p in periods)
        # photo-no-date should not be counted (it has no date_created)
        assert total_counted == 7  # 8 items - 1 without date


class TestFindTimelineGaps:
    def test_finds_gap(self):
        periods = [
            TimelinePeriod(
                start=datetime(2024, 1, 1),
                end=datetime(2024, 2, 1),
                count=10,
                label="2024-01",
            ),
            TimelinePeriod(
                start=datetime(2024, 6, 1),
                end=datetime(2024, 7, 1),
                count=5,
                label="2024-06",
            ),
        ]
        gaps = find_timeline_gaps(periods, min_gap_months=3)
        assert len(gaps) == 1
        assert gaps[0] == ("2024-01", "2024-06")

    def test_no_gap_within_threshold(self):
        periods = [
            TimelinePeriod(
                start=datetime(2024, 1, 1),
                end=datetime(2024, 2, 1),
                count=10,
                label="2024-01",
            ),
            TimelinePeriod(
                start=datetime(2024, 3, 1),
                end=datetime(2024, 4, 1),
                count=5,
                label="2024-03",
            ),
        ]
        gaps = find_timeline_gaps(periods, min_gap_months=3)
        assert len(gaps) == 0

    def test_empty_periods(self):
        assert find_timeline_gaps([]) == []


class TestAlbumDistribution:
    def test_counts_albums(self, populated_cache: CacheDB):
        dist = get_album_distribution(populated_cache)
        assert "Vacation" in dist
        assert dist["Vacation"] >= 1

    def test_empty_db(self, cache: CacheDB):
        assert get_album_distribution(cache) == {}


class TestPersonDistribution:
    def test_counts_persons(self, populated_cache: CacheDB):
        dist = get_person_distribution(populated_cache)
        assert "Alice" in dist
        assert dist["Alice"] >= 1
        assert "Bob" in dist

    def test_empty_db(self, cache: CacheDB):
        assert get_person_distribution(cache) == {}
