"""Tests for quality scoring and group ranking."""

from datetime import datetime

from media_scanner.config import Config
from media_scanner.core.quality_scorer import FORMAT_SCORES, rank_group, score_item
from media_scanner.data.models import DuplicateGroup, MatchType, MediaType
from tests.conftest import make_group, sample_item


class TestScoreItem:
    def _config(self):
        return Config()

    def test_score_in_valid_range(self):
        config = self._config()
        items = [sample_item(uuid="a"), sample_item(uuid="b")]
        group = make_group(items)
        score = score_item(items[0], group, config)
        assert 0.0 <= score <= 1.0

    def test_higher_resolution_scores_higher(self):
        config = self._config()
        high_res = sample_item(uuid="hi", width=8000, height=6000, file_size=5_000_000)
        low_res = sample_item(uuid="lo", width=640, height=480, file_size=5_000_000)
        group = make_group([high_res, low_res])

        score_hi = score_item(high_res, group, config)
        score_lo = score_item(low_res, group, config)
        assert score_hi > score_lo

    def test_raw_format_scores_higher_than_jpeg(self):
        config = self._config()
        raw = sample_item(uuid="raw", uti="com.adobe.raw-image")
        jpeg = sample_item(uuid="jpg", uti="public.jpeg")
        group = make_group([raw, jpeg])

        score_raw = score_item(raw, group, config)
        score_jpeg = score_item(jpeg, group, config)
        assert score_raw > score_jpeg

    def test_earliest_date_gets_bonus(self):
        config = self._config()
        early = sample_item(uuid="early", date_created=datetime(2020, 1, 1))
        late = sample_item(uuid="late", date_created=datetime(2024, 1, 1))
        group = make_group([early, late])

        score_early = score_item(early, group, config)
        score_late = score_item(late, group, config)
        assert score_early > score_late

    def test_edited_item_scores_higher_on_edit_status(self):
        config = self._config()
        edited = sample_item(uuid="ed", is_edited=True)
        unedited = sample_item(uuid="noed", is_edited=False)
        group = make_group([edited, unedited])

        score_ed = score_item(edited, group, config)
        score_noed = score_item(unedited, group, config)
        # Edit status is only 5%, but all else equal it should tip the scale
        assert score_ed > score_noed

    def test_metadata_completeness_matters(self):
        config = self._config()
        full = sample_item(
            uuid="full",
            has_gps=True,
            persons=["Alice"],
            keywords=["test"],
            albums=["Vacation"],
        )
        empty = sample_item(
            uuid="empty",
            has_gps=False,
            persons=[],
            keywords=[],
            albums=[],
        )
        group = make_group([full, empty])

        score_full = score_item(full, group, config)
        score_empty = score_item(empty, group, config)
        assert score_full > score_empty

    def test_apple_score_used(self):
        config = self._config()
        high = sample_item(uuid="high", apple_score=0.95)
        low = sample_item(uuid="low", apple_score=0.1)
        group = make_group([high, low])

        assert score_item(high, group, config) > score_item(low, group, config)

    def test_none_apple_score_gets_neutral(self):
        config = self._config()
        item = sample_item(uuid="none_score", apple_score=None)
        group = make_group([item, sample_item(uuid="other")])
        # Should not raise
        score = score_item(item, group, config)
        assert 0.0 <= score <= 1.0


class TestFormatScores:
    def test_raw_is_highest(self):
        assert FORMAT_SCORES["com.adobe.raw-image"] == 1.0

    def test_jpeg_is_lower_than_heic(self):
        assert FORMAT_SCORES["public.jpeg"] < FORMAT_SCORES["public.heic"]

    def test_unknown_format_gets_default(self):
        config = Config()
        item = sample_item(uuid="unknown", uti="com.some.unknown.format")
        group = make_group([item, sample_item(uuid="other")])
        # score_item uses FORMAT_SCORES.get(uti, 0.5) — should not raise
        score = score_item(item, group, config)
        assert 0.0 <= score <= 1.0


class TestRankGroup:
    def test_sets_recommended_keep_uuid(self):
        config = Config()
        high = sample_item(uuid="hi", width=8000, height=6000, file_size=10_000_000)
        low = sample_item(uuid="lo", width=640, height=480, file_size=100_000)
        group = make_group([low, high])

        ranked = rank_group(group, config)
        assert ranked.recommended_keep_uuid == "hi"

    def test_items_sorted_by_score_descending(self):
        config = Config()
        best = sample_item(uuid="best", width=8000, height=6000, file_size=10_000_000,
                           uti="com.adobe.raw-image", apple_score=0.95, is_edited=True)
        worst = sample_item(uuid="worst", width=640, height=480, file_size=100_000,
                            uti="public.jpeg", apple_score=0.1, is_edited=False,
                            has_gps=False, persons=[], keywords=[], albums=[])
        group = make_group([worst, best])

        ranked = rank_group(group, config)
        assert ranked.items[0].uuid == "best"
        assert ranked.items[1].uuid == "worst"

    def test_single_item_group(self):
        config = Config()
        item = sample_item(uuid="only")
        group = make_group([item])

        ranked = rank_group(group, config)
        assert ranked.recommended_keep_uuid == "only"


class TestCrossTypeScoring:
    def test_live_photo_preferred_over_video(self):
        """In a mixed live-photo + video group, live photo should score higher."""
        config = Config()
        live = sample_item(
            uuid="live",
            media_type=MediaType.LIVE_PHOTO,
            width=1920, height=1080,
            file_size=3_000_000,
            uti="public.heic",
        )
        video = sample_item(
            uuid="vid",
            media_type=MediaType.VIDEO,
            width=1920, height=1080,
            file_size=5_000_000,
            uti="com.apple.quicktime-movie",
        )
        group = make_group([live, video])
        ranked = rank_group(group, config)
        assert ranked.recommended_keep_uuid == "live"

    def test_no_bonus_in_same_type_group(self):
        """The media type bonus should NOT apply in video-only groups."""
        config = Config()
        v1 = sample_item(
            uuid="v1", media_type=MediaType.VIDEO,
            file_size=5_000_000, uti="com.apple.quicktime-movie",
        )
        v2 = sample_item(
            uuid="v2", media_type=MediaType.VIDEO,
            file_size=4_000_000, uti="com.apple.quicktime-movie",
        )
        group = make_group([v1, v2])
        s1 = score_item(v1, group, config)
        s2 = score_item(v2, group, config)
        # Larger file should win, no media_type bonus in play
        assert s1 > s2
