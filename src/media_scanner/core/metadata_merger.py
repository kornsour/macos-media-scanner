"""Compute metadata transfers from duplicates to keepers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from media_scanner.data.models import ActionRecord, ActionType, MediaItem, MetadataTransfer


def _best_date(
    keeper: MediaItem, all_items: list[MediaItem]
) -> tuple[datetime | None, str | None]:
    """Return the oldest date from the group if it improves the keeper's date."""
    oldest: datetime | None = None
    source_uuid: str | None = None
    for item in all_items:
        if item.date_created and (oldest is None or item.date_created < oldest):
            oldest = item.date_created
            source_uuid = item.uuid

    if oldest is None:
        return None, None
    # Transfer if keeper has no date, or keeper's date is newer than the oldest
    if keeper.date_created is None:
        return oldest, source_uuid
    if oldest < keeper.date_created:
        return oldest, source_uuid
    return None, None


def _best_gps(
    keeper: MediaItem, all_items: list[MediaItem]
) -> tuple[float | None, float | None, str | None]:
    """Return GPS from a duplicate if the keeper has none."""
    if keeper.has_gps and keeper.latitude is not None and keeper.longitude is not None:
        return None, None, None
    for item in all_items:
        if item.uuid == keeper.uuid:
            continue
        if item.has_gps and item.latitude is not None and item.longitude is not None:
            return item.latitude, item.longitude, item.uuid
    return None, None, None


def compute_transfers(
    actions: list[ActionRecord],
    items_by_uuid: dict[str, MediaItem],
) -> list[MetadataTransfer]:
    """Compute metadata transfers for groups that have KEEP + DELETE decisions."""
    # Group actions by group_id
    groups: dict[int, list[ActionRecord]] = defaultdict(list)
    for action in actions:
        if action.group_id is not None:
            groups[action.group_id].append(action)

    transfers: list[MetadataTransfer] = []
    for group_id, group_actions in groups.items():
        keeps = [a for a in group_actions if a.action == ActionType.KEEP]
        deletes = [a for a in group_actions if a.action == ActionType.DELETE]
        if not keeps or not deletes:
            continue

        keeper_uuid = keeps[0].uuid
        keeper = items_by_uuid.get(keeper_uuid)
        if not keeper:
            continue

        all_items = []
        for a in group_actions:
            item = items_by_uuid.get(a.uuid)
            if item:
                all_items.append(item)

        date, date_source = _best_date(keeper, all_items)
        lat, lon, gps_source = _best_gps(keeper, all_items)

        if date is not None or lat is not None:
            source = date_source or gps_source
            transfers.append(
                MetadataTransfer(
                    keeper_uuid=keeper_uuid,
                    group_id=group_id,
                    transfer_date=date,
                    transfer_latitude=lat,
                    transfer_longitude=lon,
                    source_uuid=source,
                )
            )

    return transfers
