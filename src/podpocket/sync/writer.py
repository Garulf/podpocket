from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Self

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import ChangeLog, User

INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)
INT32_MAX = 2**31 - 1
INT32_MIN = -(2**31)
PODCAST = "podcast"
EPISODE = "episode"
FOLDER = "folder"
PLAYLIST = "playlist"
BOOKMARK = "bookmark"
KEY_CHUNK = 500
MAX_CLOCK_SKEW_MS = 0
MAX_DECLARED_DIGITS = 19


def parse_decimal_ms(text: str) -> int | None:
    if not (text.isascii() and text.isdigit() and len(text) <= MAX_DECLARED_DIGITS):
        return None
    value = int(text)
    return value if value <= INT64_MAX else None


def wins(new_ms: int, new_value: object, old_ms: int | None, old_value: object) -> bool:
    if old_ms is None:
        return True
    if new_ms != old_ms:
        return new_ms > old_ms
    return str(new_value) > str(old_value)


class UserGone(LookupError):
    pass


async def lock_user(session: AsyncSession, user_uuid: str) -> User:
    await session.execute(
        update(User)
        .where(User.uuid == user_uuid)
        .values(last_change_ms=User.last_change_ms)
        .execution_options(synchronize_session=False)
    )
    user = await session.get(User, user_uuid, populate_existing=True)
    if user is None:
        raise UserGone(user_uuid)
    return user


APP_SOURCE = "app"


class ChangeWriter:
    def __init__(self, session: AsyncSession, user: User, *, source: str, now_ms: int):
        self.session = session
        self.user = user
        self.user_uuid = user.uuid
        self.source = source
        self.now_ms = now_ms
        self._stamp: int | None = None

    @classmethod
    async def begin(
        cls, session: AsyncSession, user_uuid: str, *, source: str, now_ms: int
    ) -> Self:
        return cls(session, await lock_user(session, user_uuid), source=source, now_ms=now_ms)

    @property
    def cursor(self) -> int:
        return self.user.last_change_ms

    def record(self, entity: str, entity_id: str, field: str, modified_ms: int) -> None:
        if self._stamp is None:
            self._stamp = max(self.now_ms, self.user.last_change_ms + 1)
            self.user.last_change_ms = self._stamp
        self.session.add(
            ChangeLog(
                user_uuid=self.user_uuid,
                entity=entity,
                entity_id=entity_id,
                field=field,
                modified_ms=modified_ms,
                server_ms=self._stamp,
                source=self.source,
            )
        )


async def changed_since(
    session: AsyncSession, user_uuid: str, cursor_ms: int
) -> defaultdict[str, set[str]]:
    rows = await session.execute(
        select(ChangeLog.entity, ChangeLog.entity_id)
        .where(ChangeLog.user_uuid == user_uuid, ChangeLog.server_ms > cursor_ms)
        .distinct()
    )
    changed: defaultdict[str, set[str]] = defaultdict(set)
    for entity, entity_id in rows:
        changed[entity].add(entity_id)
    return changed


async def load_by_keys(
    session: AsyncSession, model: Any, key_column: Any, user_uuid: str, keys: Iterable[str]
) -> list[Any]:
    ordered = sorted(set(keys))
    rows: list[Any] = []
    for start in range(0, len(ordered), KEY_CHUNK):
        chunk = ordered[start : start + KEY_CHUNK]
        rows.extend(
            await session.scalars(
                select(model).where(model.user_uuid == user_uuid, key_column.in_(chunk))
            )
        )
    return rows
