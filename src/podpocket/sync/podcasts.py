from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from podpocket.models import Subscription
from podpocket.sync.writer import PODCAST, ChangeWriter
from podpocket.timefmt import from_epoch_ms

HOME_FOLDER = "973df93c-e4dc-41fb-879e-0c7b532ebb70"
DEFAULT_EPISODES_SORT_ORDER = 3
FIELDS = (
    "subscribed",
    "folder_uuid",
    "sort_position",
    "episodes_sort_order",
    "auto_start_from",
    "auto_skip_last",
    "date_added",
)


@dataclass(frozen=True)
class PodcastChange:
    podcast_uuid: str
    subscribed: bool | None = None
    folder_uuid: str | None = None
    sort_position: int | None = None
    episodes_sort_order: int | None = None
    auto_start_from: int | None = None
    auto_skip_last: int | None = None
    date_added: datetime | None = None


def _stored(field: str, value: object) -> object:
    if field == "folder_uuid" and value in ("", HOME_FOLDER):
        return None
    return value


async def apply_podcast_changes(writer: ChangeWriter, changes: Iterable[PodcastChange]) -> set[str]:
    subscriptions: dict[str, Subscription] = {}
    changed: set[str] = set()
    for change in changes:
        uuid = change.podcast_uuid
        if not uuid:
            continue
        subscription = subscriptions.get(uuid) or await writer.session.get(
            Subscription, (writer.user_uuid, uuid)
        )
        if subscription is None:
            subscription = Subscription(
                user_uuid=writer.user_uuid, podcast_uuid=uuid, subscribed=False
            )
            writer.session.add(subscription)
        subscriptions[uuid] = subscription
        for field in FIELDS:
            value = getattr(change, field)
            if value is None:
                continue
            value = _stored(field, value)
            if getattr(subscription, field) != value:
                setattr(subscription, field, value)
                writer.record(PODCAST, uuid, field, writer.now_ms)
                changed.add(uuid)
        if subscription.subscribed and subscription.date_added is None:
            subscription.date_added = from_epoch_ms(writer.now_ms)
            writer.record(PODCAST, uuid, "date_added", writer.now_ms)
            changed.add(uuid)
    return changed
