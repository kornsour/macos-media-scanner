"""Tests for osxphotos scanner integration (all osxphotos mocked)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from media_scanner.data.models import MediaType


class TestClassifyMediaType:
    def test_video(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import _classify_media_type

            photo = MagicMock()
            photo.ismovie = True
            photo.live_photo = False
            assert _classify_media_type(photo) == MediaType.VIDEO

    def test_live_photo(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import _classify_media_type

            photo = MagicMock()
            photo.ismovie = False
            photo.live_photo = True
            assert _classify_media_type(photo) == MediaType.LIVE_PHOTO

    def test_regular_photo(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import _classify_media_type

            photo = MagicMock()
            photo.ismovie = False
            photo.live_photo = False
            assert _classify_media_type(photo) == MediaType.PHOTO


class TestPhotoToMediaItem:
    def _make_mock_photo(self, **overrides):
        photo = MagicMock()
        photo.uuid = overrides.get("uuid", "test-uuid-123")
        photo.filename = overrides.get("filename", "IMG_001.heic")
        photo.original_filename = overrides.get("original_filename", "IMG_001.heic")
        photo.path = overrides.get("path", "/photos/IMG_001.heic")
        photo.ismovie = overrides.get("ismovie", False)
        photo.live_photo = overrides.get("live_photo", False)
        photo.original_filesize = overrides.get("original_filesize", 2_000_000)
        photo.width = overrides.get("width", 4032)
        photo.height = overrides.get("height", 3024)
        photo.date = overrides.get("date", datetime(2024, 6, 15))
        photo.date_modified = overrides.get("date_modified", None)
        photo.duration = overrides.get("duration", 0)
        photo.uti = overrides.get("uti", "public.heic")
        photo.location = overrides.get("location", (37.77, -122.42))
        photo.albums = overrides.get("albums", ["Vacation"])
        photo.persons = overrides.get("persons", ["Alice"])
        photo.keywords = overrides.get("keywords", ["beach"])
        photo.hasadjustments = overrides.get("hasadjustments", False)
        photo.favorite = overrides.get("favorite", False)
        photo.hidden = overrides.get("hidden", False)
        photo.screenshot = overrides.get("screenshot", False)
        photo.selfie = overrides.get("selfie", False)
        photo.burst = overrides.get("burst", False)
        photo.burst_photos = overrides.get("burst_photos", [])
        score_mock = MagicMock()
        score_mock.overall = overrides.get("score_overall", 0.75)
        photo.score = score_mock
        return photo

    def test_basic_conversion(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo()
            item = photo_to_media_item(photo)

            assert item.uuid == "test-uuid-123"
            assert item.filename == "IMG_001.heic"
            assert item.media_type == MediaType.PHOTO
            assert item.file_size == 2_000_000
            assert item.width == 4032
            assert item.height == 3024
            assert item.has_gps is True
            assert item.latitude == 37.77
            assert item.longitude == -122.42

    def test_video_gets_duration(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo(ismovie=True, duration=30.5)
            item = photo_to_media_item(photo)

            assert item.media_type == MediaType.VIDEO
            assert item.duration == 30.5

    def test_no_gps(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo(location=(None, None))
            item = photo_to_media_item(photo)

            assert item.has_gps is False
            assert item.latitude is None
            assert item.longitude is None

    def test_no_location_attribute(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo()
            photo.location = None
            item = photo_to_media_item(photo)

            assert item.has_gps is False

    def test_albums_persons_keywords(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo(
                albums=["A", "B"],
                persons=["Alice", "Bob"],
                keywords=["sun", "sea"],
            )
            item = photo_to_media_item(photo)

            assert item.albums == ["A", "B"]
            assert item.persons == ["Alice", "Bob"]
            assert item.keywords == ["sun", "sea"]

    def test_boolean_flags(self):
        with patch.dict("sys.modules", {"osxphotos": MagicMock()}):
            from media_scanner.core.scanner import photo_to_media_item

            photo = self._make_mock_photo(
                hasadjustments=True,
                favorite=True,
                hidden=True,
                screenshot=True,
                selfie=True,
            )
            item = photo_to_media_item(photo)

            assert item.is_edited is True
            assert item.is_favorite is True
            assert item.is_hidden is True
            assert item.is_screenshot is True
            assert item.is_selfie is True


class TestScanLibrary:
    @patch("media_scanner.core.scanner.osxphotos")
    def test_movies_true_passed(self, mock_osxphotos):
        from media_scanner.core.scanner import scan_library

        mock_db = MagicMock()
        mock_osxphotos.PhotosDB.return_value = mock_db
        mock_db.photos.return_value = []

        list(scan_library())

        mock_db.photos.assert_called_once_with(movies=True)

    @patch("media_scanner.core.scanner.osxphotos")
    def test_custom_library_path(self, mock_osxphotos):
        from media_scanner.core.scanner import scan_library

        mock_db = MagicMock()
        mock_osxphotos.PhotosDB.return_value = mock_db
        mock_db.photos.return_value = []

        list(scan_library(library_path=Path("/custom/Photos.photoslibrary")))

        mock_osxphotos.PhotosDB.assert_called_once_with(
            dbfile="/custom/Photos.photoslibrary"
        )
