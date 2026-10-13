from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from podpocket.accounts.dependencies import CurrentUser
from podpocket.pcapi.wire import Int64
from podpocket.services import Services, get_services
from podpocket.sync.episodes import EpisodeChange, FieldUpdate, apply_episode_changes
from podpocket.sync.writer import ChangeWriter
from podpocket.timefmt import now_ms

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]
APP_SOURCE = "app"


class EpisodeProgress(BaseModel):
    uuid: str
    podcast: str
    position: Int64
    duration: Int64
    status: int


@router.post("/sync/update_episode", status_code=204)
async def update_episode(
    body: EpisodeProgress, user: CurrentUser, services: ServicesDep
) -> Response:
    now = now_ms()
    change = EpisodeChange(
        episode_uuid=body.uuid,
        podcast_uuid=body.podcast,
        played_up_to=FieldUpdate(body.position, now),
        duration=FieldUpdate(body.duration, now),
        playing_status=FieldUpdate(body.status, now),
    )
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user.uuid, source=APP_SOURCE, now_ms=now)
        await apply_episode_changes(writer, [change])
        await session.commit()
    return Response(status_code=204)
