from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import DeviceStats
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import wrapped
from podpocket.sync.writer import INT64_MAX, load_by_keys
from podpocket.timefmt import epoch_ms, from_epoch_ms, iso_z

LATEST_START_SECONDS = epoch_ms(datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)) // 1000
STAT_FIELDS = (
    "time_silence_removal",
    "time_skipping",
    "time_intro_skipping",
    "time_variable_speed",
    "time_listened",
)
STAT_KEYS = {
    "time_silence_removal": "timeSilenceRemoval",
    "time_skipping": "timeSkipping",
    "time_intro_skipping": "timeIntroSkipping",
    "time_variable_speed": "timeVariableSpeed",
    "time_listened": "timeListened",
}


def _incoming(record: pb.SyncUserDevice) -> dict[str, int]:
    values: dict[str, int] = {}
    device_type = wrapped(record, "device_type")
    if device_type is not None:
        values["device_type"] = device_type
    for field in ("times_started_at", *STAT_FIELDS):
        value = wrapped(record, field)
        if value is not None and value >= 0:
            values[field] = value
    return values


async def apply_device_records(
    session: AsyncSession, user_uuid: str, records: Iterable[pb.SyncUserDevice]
) -> None:
    batch = [
        (device_id, record) for record in records if (device_id := wrapped(record, "device_id"))
    ]
    rows: dict[str, DeviceStats] = {
        row.device_id: row
        for row in await load_by_keys(
            session, DeviceStats, DeviceStats.device_id, user_uuid, (id_ for id_, _ in batch)
        )
    }
    for device_id, record in batch:
        values = _incoming(record)
        row = rows.get(device_id)
        if row is None:
            if not values:
                continue
            row = DeviceStats(user_uuid=user_uuid, device_id=device_id)
            session.add(row)
            rows[device_id] = row
        for field, value in values.items():
            setattr(row, field, value)


async def stats_summary(
    session: AsyncSession, user_uuid: str, exclude_device_id: str
) -> dict[str, str]:
    rows = list(
        await session.scalars(
            select(DeviceStats).where(
                DeviceStats.user_uuid == user_uuid, DeviceStats.device_id != exclude_device_id
            )
        )
    )
    summary = {
        key: str(min(sum(getattr(row, field) or 0 for row in rows), INT64_MAX))
        for field, key in STAT_KEYS.items()
    }
    started = [
        row.times_started_at
        for row in rows
        if 0 < (row.times_started_at or 0) <= LATEST_START_SECONDS
    ]
    if started:
        summary["timesStartedAt"] = iso_z(from_epoch_ms(min(started) * 1000))
    return summary
