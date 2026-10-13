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

from podpocket.catalog import resolve_podcast_uuids
from podpocket.gpodder.limits import MAX_URL_LENGTH, StorableText
from podpocket.ids import normalize_feed_url
from podpocket.models import Episode
from podpocket.sync.episodes import COMPLETED, IN_PROGRESS, UNPLAYED, EpisodeChange, FieldUpdate
from podpocket.sync.writer import INT32_MAX, chunks
from podpocket.timefmt import epoch_ms, parse_iso

MAX_TIMESTAMP_LENGTH = 64
LATEST_EPOCH_SECONDS = 253402300799
FINISHED_TOLERANCE_SECONDS = 60

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
    if timestamp is None:
        return now_ms
    milliseconds = timestamp_ms(timestamp)
    in_current_second = milliseconds % 1000 == 0 and milliseconds // 1000 == now_ms // 1000
    return now_ms if in_current_second else milliseconds


@dataclass(frozen=True)
class EpisodeMatch:
    uuid: str
    podcast_uuid: str
    duration: float


def _finished(position: int, total: int) -> bool:
    return position > 0 and total > 0 and position >= total - FINISHED_TOLERANCE_SECONDS


def episode_change(action: EpisodeAction, episode: EpisodeMatch, now_ms: int) -> EpisodeChange:
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
    finished = _finished(position, total or int(episode.duration or 0))
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


@dataclass(frozen=True)
class EpisodeIndex:
    by_guid: dict[tuple[str, str], EpisodeMatch]
    by_url: dict[tuple[str, str], EpisodeMatch]

    def find(self, podcast_uuid: str, action: EpisodeAction) -> EpisodeMatch | None:
        found = self.by_guid.get((podcast_uuid, action.guid)) if action.guid else None
        return found or self.by_url.get((podcast_uuid, action.episode))


async def _index(session: AsyncSession, podcast_uuids: set[str], keys: set[str]) -> EpisodeIndex:
    columns = (
        Episode.uuid,
        Episode.podcast_uuid,
        Episode.guid,
        Episode.enclosure_url,
        Episode.duration,
        Episode.published,
    )
    found = {}
    for podcasts in chunks(sorted(podcast_uuids)):
        for wanted in chunks(sorted(keys)):
            rows = await session.execute(
                select(*columns).where(
                    Episode.podcast_uuid.in_(podcasts),
                    or_(Episode.guid.in_(wanted), Episode.enclosure_url.in_(wanted)),
                )
            )
            found.update((row.uuid, row) for row in rows)
    index = EpisodeIndex(by_guid={}, by_url={})
    by_uuid = sorted(found.values(), key=lambda row: row.uuid)
    for row in sorted(by_uuid, key=lambda row: row.published, reverse=True):
        match = EpisodeMatch(row.uuid, row.podcast_uuid, row.duration)
        if row.guid:
            index.by_guid.setdefault((row.podcast_uuid, row.guid), match)
        index.by_url.setdefault((row.podcast_uuid, row.enclosure_url), match)
    return index


async def match_actions(
    session: AsyncSession, actions: list[EpisodeAction]
) -> list[tuple[EpisodeAction, EpisodeMatch | None]]:
    feed_urls = [_feed_url(action.podcast) for action in actions]
    known = await resolve_podcast_uuids(session, {url for url in feed_urls if url is not None})
    podcasts = [None if url is None else known.get(url) for url in feed_urls]
    matched = [
        (action, podcast) for action, podcast in zip(actions, podcasts, strict=True) if podcast
    ]
    index = await _index(
        session,
        {podcast for _, podcast in matched},
        {key for action, _ in matched for key in (action.guid, action.episode) if key},
    )
    return [
        (action, None if podcast is None else index.find(podcast, action))
        for action, podcast in zip(actions, podcasts, strict=True)
    ]


def plan_changes(
    pairs: list[tuple[EpisodeAction, EpisodeMatch | None]], now_ms: int, ignored: int
) -> tuple[list[EpisodeChange], UploadReport]:
    newest: dict[str, tuple[tuple[int, int], EpisodeAction, EpisodeMatch]] = {}
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
