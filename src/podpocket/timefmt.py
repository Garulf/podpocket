from datetime import UTC, datetime, timedelta


def iso_z(value: datetime) -> str:
    return _to_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def pc_gmt(value: datetime) -> str:
    return _to_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime has no timezone")
    return value.astimezone(UTC)


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def epoch_ms(value: datetime) -> int:
    return (value - EPOCH) // timedelta(milliseconds=1)


def from_epoch_ms(value: int) -> datetime:
    return EPOCH + timedelta(milliseconds=value)


def plausible_published(value: datetime | None) -> datetime | None:
    return value if value is not None and value >= EPOCH else None


def now_ms() -> int:
    return epoch_ms(datetime.now(UTC))


def iso_ms(value: datetime) -> str:
    utc = _to_utc(value)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None
