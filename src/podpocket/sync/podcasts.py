from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from podpocket.models import Subscription
from podpocket.sync.writer import INT32_MAX, INT32_MIN, PODCAST, ChangeWriter, load_by_keys
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


def _is_int32(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and INT32_MIN <= value <= INT32_MAX
    )


def _acceptable(field: str, value: object) -> bool:
    match field:
        case "subscribed":
            return isinstance(value, bool)
        case "folder_uuid":
            return isinstance(value, str)
        case "date_added":
            return isinstance(value, datetime)
    return _is_int32(value)


async def apply_podcast_changes(writer: ChangeWriter, changes: Iterable[PodcastChange]) -> set[str]:
    batch = [change for change in changes if change.podcast_uuid]
    subscriptions: dict[str, Subscription] = {
        subscription.podcast_uuid: subscription
        for subscription in await load_by_keys(
            writer.session,
            Subscription,
            Subscription.podcast_uuid,
            writer.user_uuid,
            (change.podcast_uuid for change in batch),
        )
    }
    changed: set[str] = set()
    for change in batch:
        uuid = change.podcast_uuid
        values = {
            field: _stored(field, value)
            for field in FIELDS
            if (value := getattr(change, field)) is not None and _acceptable(field, value)
        }
        subscription = subscriptions.get(uuid)
        if subscription is None:
            if not values:
                continue
            subscription = Subscription(
                user_uuid=writer.user_uuid, podcast_uuid=uuid, subscribed=False
            )
            writer.session.add(subscription)
            subscriptions[uuid] = subscription
        for field, value in values.items():
            if getattr(subscription, field) != value:
                setattr(subscription, field, value)
                writer.record(PODCAST, uuid, field, writer.now_ms)
                changed.add(uuid)
        if subscription.subscribed and subscription.date_added is None:
            subscription.date_added = from_epoch_ms(writer.now_ms)
            writer.record(PODCAST, uuid, "date_added", writer.now_ms)
            changed.add(uuid)
    return changed
