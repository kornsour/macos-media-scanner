"""Tests for CacheDB metadata transfer methods."""

from datetime import datetime

from media_scanner.data.cache import CacheDB
from media_scanner.data.models import MetadataTransfer


class TestSaveAndRetrieve:
    def test_save_and_get_pending(self, tmp_path):
        cache = CacheDB(tmp_path / "test.db")
        transfer = MetadataTransfer(
            keeper_uuid="keeper-1",
            group_id=1,
            transfer_date=datetime(2020, 1, 1),
            transfer_latitude=37.7,
            transfer_longitude=-122.4,
            source_uuid="dupe-1",
        )
        cache.save_metadata_transfer(transfer)

        pending = cache.get_pending_transfers()
        assert len(pending) == 1
        assert pending[0].keeper_uuid == "keeper-1"
        assert pending[0].group_id == 1
        assert pending[0].transfer_date == datetime(2020, 1, 1)
        assert pending[0].transfer_latitude == 37.7
        assert pending[0].transfer_longitude == -122.4
        assert pending[0].source_uuid == "dupe-1"
        assert pending[0].applied is False
        cache.close()

    def test_save_date_only(self, tmp_path):
        cache = CacheDB(tmp_path / "test.db")
        transfer = MetadataTransfer(
            keeper_uuid="keeper-1",
            group_id=1,
            transfer_date=datetime(2020, 6, 15),
            source_uuid="dupe-1",
        )
        cache.save_metadata_transfer(transfer)

        pending = cache.get_pending_transfers()
        assert len(pending) == 1
        assert pending[0].transfer_date == datetime(2020, 6, 15)
        assert pending[0].transfer_latitude is None
        assert pending[0].transfer_longitude is None
        cache.close()


class TestMarkApplied:
    def test_mark_applied(self, tmp_path):
        cache = CacheDB(tmp_path / "test.db")
        transfer = MetadataTransfer(
            keeper_uuid="keeper-1",
            group_id=1,
            transfer_date=datetime(2020, 1, 1),
            source_uuid="dupe-1",
        )
        cache.save_metadata_transfer(transfer)

        cache.mark_transfer_applied("keeper-1", 1)

        pending = cache.get_pending_transfers()
        assert len(pending) == 0
        cache.close()

    def test_mark_applied_with_error(self, tmp_path):
        cache = CacheDB(tmp_path / "test.db")
        transfer = MetadataTransfer(
            keeper_uuid="keeper-1",
            group_id=1,
            transfer_date=datetime(2020, 1, 1),
            source_uuid="dupe-1",
        )
        cache.save_metadata_transfer(transfer)

        cache.mark_transfer_applied("keeper-1", 1, error="Asset not found")

        pending = cache.get_pending_transfers()
        assert len(pending) == 0

        # Verify error was recorded
        row = cache.conn.execute(
            "SELECT error_message FROM metadata_transfers WHERE keeper_uuid = ?",
            ("keeper-1",),
        ).fetchone()
        assert row["error_message"] == "Asset not found"
        cache.close()


class TestClearPending:
    def test_clear_pending(self, tmp_path):
        cache = CacheDB(tmp_path / "test.db")
        for i in range(3):
            cache.save_metadata_transfer(
                MetadataTransfer(
                    keeper_uuid=f"keeper-{i}",
                    group_id=i,
                    transfer_date=datetime(2020, 1, 1),
                )
            )

        # Mark one as applied
        cache.mark_transfer_applied("keeper-0", 0)

        cache.clear_pending_transfers()

        pending = cache.get_pending_transfers()
        assert len(pending) == 0

        # Applied transfer should still exist
        row = cache.conn.execute(
            "SELECT COUNT(*) as c FROM metadata_transfers WHERE applied = 1"
        ).fetchone()
        assert row["c"] == 1
        cache.close()
