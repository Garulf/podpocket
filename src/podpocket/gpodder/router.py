import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.gpodder.auth import GpodderRoute, GpodderUser
from podpocket.gpodder.limits import (
    MAX_ACTIONS,
    MAX_SUBSCRIPTION_CHANGES,
    StorableText,
    read_json,
)
from podpocket.gpodder.subscriptions import (
    AddedAndRemoved,
    apply_subscription_upload,
    clean_upload,
    ensure_podcasts,
    subscription_delta,
)
from podpocket.gpodder.uploads import match_actions, parse_actions, plan_changes
from podpocket.models import User
from podpocket.services import Services, get_services
from podpocket.sync.episodes import apply_episode_changes
from podpocket.sync.writer import GPODDER_SOURCE, INT64_MAX, ChangeWriter, UserGone
from podpocket.timefmt import now_ms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/2", route_class=GpodderRoute)
ServicesDep = Annotated[Services, Depends(get_services)]
Since = Annotated[int, Query(ge=0, le=INT64_MAX)]
DeviceId = Annotated[str, Path(max_length=255)]


class SubscriptionChanges(BaseModel):
    add: list[StorableText] = Field(default_factory=list)
    remove: list[StorableText] = Field(default_factory=list)


def _change_count(body: Any) -> int:
    if not isinstance(body, dict):
        return 0
    return sum(
        len(urls) for urls in (body.get("add"), body.get("remove")) if isinstance(urls, list)
    )


def _conflict() -> HTTPException:
    return HTTPException(status_code=400, detail="a feed was both added and removed")


async def _cursor(session: AsyncSession, user: User) -> int:
    cursor = await session.scalar(select(User.last_change_ms).where(User.uuid == user.uuid))
    if cursor is None:
        raise UserGone(user.uuid)
    return cursor


@router.post("/auth/{username}/login.json")
async def login(user: GpodderUser) -> Response:
    return Response(status_code=200)


@router.post("/auth/{username}/logout.json")
async def logout(user: GpodderUser) -> Response:
    return Response(status_code=200)


@router.get("/subscriptions/{username}/{device}.json")
async def get_subscriptions(
    device: DeviceId, user: GpodderUser, services: ServicesDep, since: Since = 0
) -> dict[str, Any]:
    async with services.sessionmaker() as session:
        cursor = await _cursor(session, user)
        delta = await subscription_delta(session, user.uuid, since, cursor)
    return {"add": delta.add, "remove": delta.remove, "timestamp": cursor}


@router.post("/subscriptions/{username}/{device}.json")
async def upload_subscriptions(
    device: DeviceId, request: Request, user: GpodderUser, services: ServicesDep
) -> dict[str, Any]:
    body = await read_json(request)
    if _change_count(body) > MAX_SUBSCRIPTION_CHANGES:
        raise HTTPException(status_code=413, detail="too many subscription changes")
    try:
        changes = SubscriptionChanges.model_validate(body)
        upload = clean_upload(changes.add, changes.remove)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="invalid subscription changes") from exc
    except AddedAndRemoved as exc:
        raise _conflict() from exc
    await ensure_podcasts(services.sessionmaker, upload.add)
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(
            session, user.uuid, source=GPODDER_SOURCE, now_ms=now_ms()
        )
        try:
            await apply_subscription_upload(writer, upload)
        except AddedAndRemoved as exc:
            raise _conflict() from exc
        cursor = writer.cursor
        await session.commit()
    return {"timestamp": cursor, "update_urls": upload.update_urls}


@router.post("/episodes/{username}.json")
async def upload_episode_actions(
    request: Request, user: GpodderUser, services: ServicesDep
) -> dict[str, Any]:
    body = await read_json(request)
    if not isinstance(body, list):
        raise HTTPException(status_code=400, detail="expected a list of episode actions")
    if len(body) > MAX_ACTIONS:
        raise HTTPException(status_code=413, detail="too many episode actions")
    actions, ignored = parse_actions(body)
    now = now_ms()
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user.uuid, source=GPODDER_SOURCE, now_ms=now)
        changes, report = plan_changes(await match_actions(session, actions), now, ignored)
        await apply_episode_changes(writer, changes)
        cursor = writer.cursor
        await session.commit()
    logger.info(
        "gpodder episode actions: %d applied, %d unmatched, %d ignored",
        report.applied,
        report.unmatched,
        report.ignored,
    )
    return {"timestamp": cursor, "update_urls": []}
