import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import NamedSetting
from podpocket.sync.writer import INT64_MAX, INT64_MIN, is_clean_text, lock_user

MAX_NAME_LENGTH = 64
SCALARS = (bool, int, float, str)


async def update_named_settings(
    session: AsyncSession, user_uuid: str, incoming: dict[str, Any], now_ms: int
) -> dict[str, dict[str, Any]]:
    await lock_user(session, user_uuid)
    rows = {
        row.name: row
        for row in await session.scalars(
            select(NamedSetting).where(NamedSetting.user_uuid == user_uuid)
        )
    }
    sent: set[str] = set()
    for name, value in incoming.items():
        if not _storable(name, value):
            continue
        row = rows.get(name)
        if row is None:
            row = NamedSetting(user_uuid=user_uuid, name=name)
            session.add(row)
            rows[name] = row
        row.value = value
        row.modified_ms = now_ms
        sent.add(name)
    return {
        name: {"value": row.value, "changed": name not in sent}
        for name, row in sorted(rows.items())
    }


def _storable(name: str, value: Any) -> bool:
    if not name or len(name) > MAX_NAME_LENGTH or not is_clean_text(name):
        return False
    if not isinstance(value, SCALARS):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, int):
        return INT64_MIN <= value <= INT64_MAX
    return is_clean_text(value)
