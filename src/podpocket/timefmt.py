from datetime import UTC, datetime


def iso_z(value: datetime) -> str:
    return _to_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def pc_gmt(value: datetime) -> str:
    return _to_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime has no timezone")
    return value.astimezone(UTC)
