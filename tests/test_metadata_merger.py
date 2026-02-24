"""Tests for metadata merger logic."""

from datetime import datetime
from pathlib import Path

from media_scanner.core.metadata_merger import compute_transfers
from media_scanner.data.models import (
    ActionRecord,
    ActionType,
    MediaItem,
    MediaType,
)


def _make_item(
    uuid: str,
    date_created: datetime | None = None,
    has_gps: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> MediaItem:
    return MediaItem(
        uuid=uuid,
        filename=f"{uuid}.jpg",
        original_filename=f"{uuid}.jpg",
        path=Path(f"/photos/{uuid}.jpg"),
        media_type=MediaType.PHOTO,
        file_size=1000,
        width=100,
        height=100,
        date_created=date_created,
        date_modified=None,
        duration=None,
        uti="public.jpeg",
        has_gps=has_gps,
        latitude=latitude,
        longitude=longitude,
    )


def _make_action(uuid: str, action: ActionType, group_id: int) -> ActionRecord:
    return ActionRecord(uuid=uuid, action=action, group_id=group_id)


class TestDateTransfer:
    def test_keeper_missing_date_gets_oldest(self):
        keeper = _make_item("keeper", date_created=None)
        dupe = _make_item("dupe", date_created=datetime(2020, 1, 1))
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 1
        assert transfers[0].transfer_date == datetime(2020, 1, 1)
        assert transfers[0].keeper_uuid == "keeper"

    def test_keeper_newer_date_gets_oldest(self):
        keeper = _make_item("keeper", date_created=datetime(2023, 6, 15))
        dupe = _make_item("dupe", date_created=datetime(2020, 1, 1))
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 1
        assert transfers[0].transfer_date == datetime(2020, 1, 1)

    def test_keeper_already_oldest_no_transfer(self):
        keeper = _make_item("keeper", date_created=datetime(2020, 1, 1))
        dupe = _make_item("dupe", date_created=datetime(2023, 6, 15))
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 0


class TestGPSTransfer:
    def test_keeper_missing_gps_inherits(self):
        keeper = _make_item("keeper")
        dupe = _make_item("dupe", has_gps=True, latitude=37.7, longitude=-122.4)
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 1
        assert transfers[0].transfer_latitude == 37.7
        assert transfers[0].transfer_longitude == -122.4

    def test_keeper_has_gps_no_transfer(self):
        keeper = _make_item("keeper", has_gps=True, latitude=37.7, longitude=-122.4)
        dupe = _make_item("dupe", has_gps=True, latitude=40.7, longitude=-74.0)
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 0


class TestEdgeCases:
    def test_no_metadata_to_transfer(self):
        keeper = _make_item("keeper", date_created=datetime(2020, 1, 1))
        dupe = _make_item("dupe", date_created=datetime(2023, 6, 15))
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 0

    def test_keep_all_no_transfers(self):
        a = _make_item("a", date_created=datetime(2020, 1, 1))
        b = _make_item("b", date_created=datetime(2023, 6, 15))
        items = {"a": a, "b": b}
        actions = [
            _make_action("a", ActionType.KEEP, 1),
            _make_action("b", ActionType.KEEP, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 0

    def test_empty_input(self):
        assert compute_transfers([], {}) == []

    def test_combined_date_and_gps(self):
        keeper = _make_item("keeper", date_created=datetime(2023, 6, 15))
        dupe = _make_item(
            "dupe",
            date_created=datetime(2020, 1, 1),
            has_gps=True,
            latitude=37.7,
            longitude=-122.4,
        )
        items = {"keeper": keeper, "dupe": dupe}
        actions = [
            _make_action("keeper", ActionType.KEEP, 1),
            _make_action("dupe", ActionType.DELETE, 1),
        ]
        transfers = compute_transfers(actions, items)
        assert len(transfers) == 1
        assert transfers[0].transfer_date == datetime(2020, 1, 1)
        assert transfers[0].transfer_latitude == 37.7
        assert transfers[0].transfer_longitude == -122.4
