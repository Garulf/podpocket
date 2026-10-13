from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from podpocket.accounts.dependencies import AuthenticatedRoute, CurrentUser
from podpocket.models import Episode, Podcast
from podpocket.pcapi.wire import EncodableText
from podpocket.services import Services, get_services

router = APIRouter(prefix="/api/podpocket", route_class=AuthenticatedRoute)
ServicesDep = Annotated[Services, Depends(get_services)]

MAX_UUIDS = 500
UUID_LENGTH = 36


class EpisodeSourcesBody(BaseModel):
    uuids: Annotated[
        list[Annotated[EncodableText, Field(max_length=UUID_LENGTH)]], Field(max_length=MAX_UUIDS)
    ]


@router.post("/episode_sources")
async def episode_sources(
    body: EpisodeSourcesBody, user: CurrentUser, services: ServicesDep
) -> dict[str, list[dict[str, str | None]]]:
    query = (
        select(Episode.uuid, Podcast.feed_url, Episode.guid, Episode.enclosure_url)
        .join(Podcast, Podcast.uuid == Episode.podcast_uuid)
        .where(Episode.uuid.in_(set(body.uuids)))
        .order_by(Episode.uuid)
    )
    async with services.sessionmaker() as session:
        rows = (await session.execute(query)).all()
    return {
        "episodes": [
            {"uuid": uuid, "feed_url": feed_url, "guid": guid, "enclosure_url": enclosure_url}
            for uuid, feed_url, guid, enclosure_url in rows
        ]
    }
