import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from podpocket import catalog
from podpocket.accounts.dependencies import CurrentUser
from podpocket.models import (
    Bookmark,
    Episode,
    Folder,
    Playlist,
    Podcast,
    Subscription,
    UserEpisodeState,
)
from podpocket.pcapi.records import podcast_list_entry
from podpocket.pcapi.wire import is_protobuf, protobuf_response, read_message
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import to_timestamp
from podpocket.services import Services, get_services
from podpocket.sync.bookmarks import bookmark_response
from podpocket.sync.folders import podcast_folder
from podpocket.sync.playlists import playlist_list_entry
from podpocket.sync.podcasts import DEFAULT_EPISODES_SORT_ORDER
from podpocket.sync.writer import load_by_keys

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]
NOT_PLAYED = 1
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
JSON_EPISODE_FIELDS = (
    ("duration", "duration"),
    ("playing_status", "playingStatus"),
    ("played_up_to", "playedUpTo"),
    ("archived", "isDeleted"),
    ("starred", "starred"),
    ("deselected_chapters", "deselectedChapters"),
)


class PodcastEpisodesRequest(BaseModel):
    uuid: str


def _known(state: UserEpisodeState | None, field: str, default: Any) -> Any:
    value = None if state is None else getattr(state, field)
    return default if value is None else value


def _int32(value: float) -> int:
    if math.isnan(value):
        return 0
    return int(min(max(value, INT32_MIN), INT32_MAX))


@router.post("/user/podcast/list")
async def podcast_list(user: CurrentUser, services: ServicesDep) -> Response:
    async with services.sessionmaker() as session:
        subscriptions = await session.scalars(
            select(Subscription)
            .where(Subscription.user_uuid == user.uuid, Subscription.subscribed.is_(True))
            .order_by(Subscription.podcast_uuid)
        )
        podcasts = [podcast_list_entry(subscription) for subscription in subscriptions]
        folders = await session.scalars(
            select(Folder)
            .where(Folder.user_uuid == user.uuid, Folder.is_deleted.is_(False))
            .order_by(Folder.folder_uuid)
        )
        response = pb.UserPodcastListResponse(
            podcasts=podcasts, folders=[podcast_folder(folder) for folder in folders]
        )
    return protobuf_response(response)


@router.post("/user/podcast/episodes")
async def podcast_episodes(
    body: PodcastEpisodesRequest, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    async with services.sessionmaker() as session:
        subscription = await session.get(Subscription, (user.uuid, body.uuid))
        states = list(
            await session.scalars(
                select(UserEpisodeState)
                .where(
                    UserEpisodeState.user_uuid == user.uuid,
                    UserEpisodeState.podcast_uuid == body.uuid,
                )
                .order_by(UserEpisodeState.episode_uuid)
            )
        )
    response: dict[str, Any] = {
        "subscribed": bool(subscription and subscription.subscribed),
        "episodes": [_episode_json(state) for state in states],
    }
    if subscription is not None:
        response["episodesSortOrder"] = (
            subscription.episodes_sort_order
            if subscription.episodes_sort_order is not None
            else DEFAULT_EPISODES_SORT_ORDER
        )
        response["autoStartFrom"] = subscription.auto_start_from or 0
    return response


def _episode_json(state: UserEpisodeState) -> dict[str, Any]:
    body: dict[str, Any] = {"uuid": state.episode_uuid}
    for column, key in JSON_EPISODE_FIELDS:
        value = getattr(state, column)
        if value is not None:
            body[key] = value
    return body


@router.post("/user/episodes")
async def episodes(request: Request, user: CurrentUser, services: ServicesDep) -> Response:
    message = await read_message(request, pb.PodcastsEpisodesRequest)
    found: list[tuple[Episode, Podcast]] = []
    async with services.sessionmaker() as session:
        for podcast_uuid, episode_uuid in zip(
            message.podcast_uuids, message.episode_uuids, strict=False
        ):
            episode = await catalog.find_episode(session, podcast_uuid, episode_uuid)
            podcast = await session.get(Podcast, podcast_uuid) if episode is not None else None
            if episode is not None and podcast is not None:
                found.append((episode, podcast))
        states = {
            state.episode_uuid: state
            for state in await load_by_keys(
                session,
                UserEpisodeState,
                UserEpisodeState.episode_uuid,
                user.uuid,
                [episode.uuid for episode, _ in found],
            )
        }
    response = pb.EpisodesResponse(
        total=len(found),
        episodes=[
            _episode_response(episode, podcast, states.get(episode.uuid))
            for episode, podcast in found
        ],
    )
    return protobuf_response(response)


def _episode_response(
    episode: Episode, podcast: Podcast, state: UserEpisodeState | None
) -> pb.EpisodeResponse:
    return pb.EpisodeResponse(
        uuid=episode.uuid,
        url=episode.enclosure_url,
        published=to_timestamp(episode.published),
        duration=_int32(episode.duration),
        file_type=episode.file_type,
        title=episode.title,
        size=episode.file_size,
        playing_status=_int32(_known(state, "playing_status", NOT_PLAYED)),
        played_up_to=_int32(_known(state, "played_up_to", 0)),
        starred=_known(state, "starred", False),
        podcast_uuid=episode.podcast_uuid,
        podcast_title=podcast.title,
        episode_type=episode.episode_type,
        episode_season=_int32(episode.season or 0),
        episode_number=_int32(episode.number or 0),
        is_deleted=_known(state, "archived", False),
        author=podcast.author,
    )


@router.post("/user/playlist/list")
async def playlist_list(request: Request, user: CurrentUser, services: ServicesDep) -> Response:
    if not is_protobuf(request):
        return JSONResponse({"playlists": []})
    async with services.sessionmaker() as session:
        playlists = await session.scalars(
            select(Playlist).where(Playlist.user_uuid == user.uuid).order_by(Playlist.uuid)
        )
        response = pb.UserPlaylistListResponse(
            playlists=[playlist_list_entry(playlist) for playlist in playlists]
        )
    return protobuf_response(response)


@router.post("/user/bookmark/list")
async def bookmark_list(user: CurrentUser, services: ServicesDep) -> Response:
    async with services.sessionmaker() as session:
        bookmarks = await session.scalars(
            select(Bookmark)
            .where(Bookmark.user_uuid == user.uuid, Bookmark.is_deleted.is_(False))
            .order_by(Bookmark.created_at, Bookmark.bookmark_uuid)
        )
        response = pb.BookmarksResponse(
            bookmarks=[bookmark_response(bookmark) for bookmark in bookmarks]
        )
    return protobuf_response(response)


@router.post("/starred/list")
async def starred_list(user: CurrentUser, services: ServicesDep) -> Response:
    async with services.sessionmaker() as session:
        states = await session.scalars(
            select(UserEpisodeState)
            .where(UserEpisodeState.user_uuid == user.uuid, UserEpisodeState.starred.is_(True))
            .order_by(UserEpisodeState.starred_modified.desc(), UserEpisodeState.episode_uuid)
        )
        response = pb.StarredEpisodesResponse(
            episodes=[
                pb.StarredEpisode(
                    uuid=state.episode_uuid,
                    podcast_uuid=state.podcast_uuid,
                    duration=_int32(_known(state, "duration", 0)),
                    playing_status=_int32(_known(state, "playing_status", NOT_PLAYED)),
                    played_up_to=_int32(_known(state, "played_up_to", 0)),
                    is_deleted=_known(state, "archived", False),
                    starred_modified=_known(state, "starred_modified", 0),
                )
                for state in states
            ]
        )
    return protobuf_response(response)
