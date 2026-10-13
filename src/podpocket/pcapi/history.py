from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from podpocket.accounts.dependencies import CurrentUser
from podpocket.models import HistoryEntry
from podpocket.pcapi.wire import MAX_DECLARED_DIGITS, Int64
from podpocket.services import Services, get_services
from podpocket.sync.history import HistoryChange, history_for_year, sync_history
from podpocket.sync.writer import INT64_MAX
from podpocket.timefmt import iso_z, now_ms, parse_iso

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]


class HistoryChangeBody(BaseModel):
    action: int
    modifiedAt: str
    episode: str | None = None
    podcast: str | None = None
    title: str | None = None
    url: str | None = None
    published: str | None = None


class HistorySyncBody(BaseModel):
    changes: list[HistoryChangeBody] = []
    deviceTime: int = 0
    serverModified: Int64 = 0
    version: int = 1


class HistoryYearBody(BaseModel):
    version: str = "1"
    count: bool = False
    year: int


def _modified_ms(text: str) -> int | None:
    if not (text.isascii() and text.isdigit() and len(text) <= MAX_DECLARED_DIGITS):
        return None
    value = int(text)
    return value if value <= INT64_MAX else None


def _change(body: HistoryChangeBody) -> HistoryChange | None:
    modified_ms = _modified_ms(body.modifiedAt)
    if modified_ms is None:
        return None
    return HistoryChange(
        action=body.action,
        modified_ms=modified_ms,
        episode_uuid=body.episode,
        podcast_uuid=body.podcast,
        title=body.title,
        url=body.url,
        published=parse_iso(body.published),
    )


def _change_json(entry: HistoryEntry) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": entry.action,
        "modifiedAt": str(entry.modified_ms),
        "episode": entry.episode_uuid,
    }
    optional = (
        ("podcast", entry.podcast_uuid),
        ("title", entry.title),
        ("url", entry.url),
        ("published", iso_z(entry.published) if entry.published else None),
    )
    for key, value in optional:
        if value is not None:
            body[key] = value
    return body


@router.post("/history/sync")
async def history_sync(
    body: HistorySyncBody, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    changes = [change for change in map(_change, body.changes) if change is not None]
    async with services.sessionmaker() as session:
        state = await sync_history(session, user.uuid, changes, body.serverModified, now_ms())
        await session.commit()
    return {
        "serverModified": state.server_modified,
        "lastCleared": state.last_cleared,
        "changes": [_change_json(entry) for entry in state.entries],
    }


@router.post("/history/year")
async def history_year(
    body: HistoryYearBody, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    async with services.sessionmaker() as session:
        entries = await history_for_year(session, user.uuid, body.year)
    if body.count:
        return {"count": len(entries)}
    return {
        "history": {
            "serverModified": user.history_modified_ms,
            "lastCleared": user.history_cleared_ms,
            "changes": [_change_json(entry) for entry in entries],
        }
    }
