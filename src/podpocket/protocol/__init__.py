from datetime import UTC, datetime
from typing import Any

from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp


def to_timestamp(value: datetime) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(value)
    return stamp


def from_timestamp(value: Timestamp) -> datetime | None:
    try:
        return value.ToDatetime(tzinfo=UTC)
    except ValueError:
        return None


def wrapped(message: Message, name: str) -> Any | None:
    return getattr(message, name).value if message.HasField(name) else None
