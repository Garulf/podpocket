from datetime import UTC, datetime


def utc() -> datetime:
    return datetime.now(UTC)
