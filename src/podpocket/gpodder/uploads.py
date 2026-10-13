from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.catalog import find_podcast_by_url
from podpocket.gpodder.limits import MAX_URL_LENGTH, StorableText
from podpocket.ids import normalize_feed_url
from podpocket.models import Episode
from podpocket.sync.episodes import COMPLETED, IN_PROGRESS, UNPLAYED, EpisodeChange, FieldUpdate
from podpocket.sync.writer import INT32_MAX, KEY_CHUNK
from podpocket.timefmt import epoch_ms, parse_iso

MAX_TIMESTAMP_LENGTH = 64
LATEST_EPOCH_SECONDS = 253402300799

Url = Annotated[StorableText, Field(max_length=MAX_URL_LENGTH)]
Seconds = Annotated[float, Field(strict=True, ge=0, le=INT32_MAX, allow_inf_nan=False)]


class EpisodeAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    podcast: Url
    episode: Url
    action: Annotated[StorableText, Field(max_length=16)]
    guid: Url | None = None
    timestamp: Annotated[StrictStr, Field(max_length=MAX_TIMESTAMP_LENGTH)] | StrictInt | None = (
        None
    )
    position: Seconds | None = None
    total: Seconds | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_usable(cls, timestamp: str | int | None) -> str | int | None:
        if timestamp is not None:
            timestamp_ms(timestamp)
        return timestamp

    @property
    def kind(self) -> str:
        return self.action.lower()


ACTION = TypeAdapter(EpisodeAction)


@dataclass(frozen=True)
class UploadReport:
    applied: int
    unmatched: int
    ignored: int


def parse_actions(items: list[Any]) -> tuple[list[EpisodeAction], int]:
    actions: list[EpisodeAction] = []
    ignored = 0
    for raw in items:
        try:
            action = ACTION.validate_python(raw)
        except ValidationError:
            ignored += 1
            continue
        if action.kind == "new" or (action.kind == "play" and action.position is not None):
            actions.append(action)
        else:
            ignored += 1
    return actions, ignored


def timestamp_ms(timestamp: str | int) -> int:
    if isinstance(timestamp, int):
        if not 0 <= timestamp <= LATEST_EPOCH_SECONDS:
            raise ValueError("epoch seconds out of range")
        return timestamp * 1000
    parsed = None if timestamp.strip().isdigit() else parse_iso(timestamp)
    if parsed is None or (milliseconds := epoch_ms(parsed)) < 0:
        raise ValueError("unusable timestamp")
    return milliseconds


def action_ms(timestamp: str | int | None, now_ms: int) -> int:
    return now_ms if timestamp is None else timestamp_ms(timestamp)


def episode_change(action: EpisodeAction, episode: Episode, now_ms: int) -> EpisodeChange:
    modified = action_ms(action.timestamp, now_ms)
    if action.kind == "new":
        return EpisodeChange(
            episode_uuid=episode.uuid,
            podcast_uuid=episode.podcast_uuid,
            playing_status=FieldUpdate(UNPLAYED, modified),
            played_up_to=FieldUpdate(0, modified),
        )
    position = int(action.position or 0)
    total = int(action.total or 0)
    finished = total > 0 and position >= total
    return EpisodeChange(
        episode_uuid=episode.uuid,
        podcast_uuid=episode.podcast_uuid,
        playing_status=FieldUpdate(COMPLETED if finished else IN_PROGRESS, modified),
        played_up_to=FieldUpdate(position, modified),
        duration=FieldUpdate(total, modified) if total > 0 else None,
    )


def _feed_url(raw_url: str) -> str | None:
    try:
        return normalize_feed_url(raw_url)
    except ValueError:
        return None


async def _candidates(
    session: AsyncSession, podcast_uuid: str, actions: list[EpisodeAction]
) -> list[Episode]:
    keys = sorted({key for action in actions for key in (action.guid, action.episode) if key})
    found: list[Episode] = []
    for start in range(0, len(keys), KEY_CHUNK):
        chunk = keys[start : start + KEY_CHUNK]
        found.extend(
            await session.scalars(
                select(Episode)
                .where(
                    Episode.podcast_uuid == podcast_uuid,
                    or_(Episode.guid.in_(chunk), Episode.enclosure_url.in_(chunk)),
                )
                .order_by(Episode.published.desc(), Episode.uuid)
            )
        )
    return found


@dataclass(frozen=True)
class EpisodeIndex:
    by_guid: dict[str, Episode]
    by_url: dict[str, Episode]

    def find(self, action: EpisodeAction) -> Episode | None:
        found = self.by_guid.get(action.guid) if action.guid else None
        return found or self.by_url.get(action.episode)


async def _index(session: AsyncSession, url: str, group: list[EpisodeAction]) -> EpisodeIndex:
    index = EpisodeIndex(by_guid={}, by_url={})
    podcast = await find_podcast_by_url(session, url)
    if podcast is None:
        return index
    for episode in await _candidates(session, podcast.uuid, group):
        if episode.guid:
            index.by_guid.setdefault(episode.guid, episode)
        index.by_url.setdefault(episode.enclosure_url, episode)
    return index


async def match_actions(
    session: AsyncSession, actions: list[EpisodeAction]
) -> list[tuple[EpisodeAction, Episode | None]]:
    feed_urls = [_feed_url(action.podcast) for action in actions]
    groups: defaultdict[str, list[EpisodeAction]] = defaultdict(list)
    for action, url in zip(actions, feed_urls, strict=True):
        if url is not None:
            groups[url].append(action)
    indexes = {url: await _index(session, url, group) for url, group in groups.items()}
    return [
        (action, indexes[url].find(action) if url is not None else None)
        for action, url in zip(actions, feed_urls, strict=True)
    ]


def plan_changes(
    pairs: list[tuple[EpisodeAction, Episode | None]], now_ms: int, ignored: int
) -> tuple[list[EpisodeChange], UploadReport]:
    newest: dict[str, tuple[tuple[int, int], EpisodeAction, Episode]] = {}
    for order, (action, episode) in enumerate(pairs):
        if episode is None:
            continue
        rank = (action_ms(action.timestamp, now_ms), order)
        if episode.uuid not in newest or rank > newest[episode.uuid][0]:
            newest[episode.uuid] = (rank, action, episode)
    changes = [episode_change(action, episode, now_ms) for _, action, episode in newest.values()]
    report = UploadReport(
        applied=len(changes),
        unmatched=sum(1 for _, episode in pairs if episode is None),
        ignored=ignored,
    )
    return changes, report
