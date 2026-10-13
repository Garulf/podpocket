from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from podpocket.accounts.dependencies import CurrentUser
from podpocket.models import HistoryEntry, User
from podpocket.pcapi.wire import EncodableText, Int64
from podpocket.services import Services, get_services
from podpocket.sync.history import HistoryChange, history_for_year, sync_history
from podpocket.sync.writer import parse_decimal_ms
from podpocket.timefmt import iso_z, now_ms, parse_iso

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]


class HistoryChangeBody(BaseModel):
    action: int
    modifiedAt: EncodableText
    episode: EncodableText | None = None
    podcast: EncodableText | None = None
    title: EncodableText | None = None
    url: EncodableText | None = None
    published: EncodableText | None = None


class HistorySyncBody(BaseModel):
    changes: list[HistoryChangeBody] = []
    deviceTime: int = 0
    serverModified: Int64 = 0
    version: int = 1


class HistoryYearBody(BaseModel):
    version: str = "1"
    count: bool = False
    year: int


def _change(body: HistoryChangeBody) -> HistoryChange | None:
    modified_ms = parse_decimal_ms(body.modifiedAt)
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
        stored = await session.get(User, user.uuid, populate_existing=True)
        entries = await history_for_year(session, user.uuid, body.year)
    if body.count:
        return {"count": len(entries)}
    return {
        "history": {
            "serverModified": stored.history_modified_ms if stored else 0,
            "lastCleared": stored.history_cleared_ms if stored else 0,
            "changes": [_change_json(entry) for entry in entries],
        }
    }
