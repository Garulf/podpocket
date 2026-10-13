from datetime import UTC, datetime, timedelta, timezone

import pytest

from podpocket.timefmt import epoch_ms, from_epoch_ms, iso_ms, iso_z, now_ms, parse_iso, pc_gmt


def test_iso_z_formats_utc_without_fraction():
    value = datetime(2026, 10, 12, 6, 0, 0, 123456, tzinfo=UTC)

    assert iso_z(value) == "2026-10-12T06:00:00Z"


def test_formats_convert_offsets_to_utc():
    value = datetime(2026, 10, 12, 8, 30, tzinfo=timezone(timedelta(hours=2)))

    assert iso_z(value) == "2026-10-12T06:30:00Z"
    assert pc_gmt(value) == "2026-10-12 06:30:00"


@pytest.mark.parametrize("format_time", [iso_z, pc_gmt])
def test_formats_reject_naive_datetimes(format_time):
    with pytest.raises(ValueError):
        format_time(datetime(2026, 10, 12, 6, 0, 0))


def test_epoch_ms_round_trips():
    value = datetime(2026, 10, 12, 10, 0, 0, 123000, tzinfo=UTC)

    assert epoch_ms(value) == 1791799200123
    assert from_epoch_ms(1791799200123) == value


def test_iso_ms_keeps_milliseconds_in_utc():
    assert (
        iso_ms(datetime(2026, 10, 12, 10, 0, 0, 123456, tzinfo=UTC)) == "2026-10-12T10:00:00.123Z"
    )
    shifted = datetime(2026, 10, 12, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso_ms(shifted) == "2026-10-12T10:00:00.000Z"


def test_iso_ms_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        iso_ms(datetime(2026, 10, 12, 10, 0))


def test_now_ms_is_the_current_time():
    before = epoch_ms(datetime.now(UTC))
    value = now_ms()
    after = epoch_ms(datetime.now(UTC))

    assert before <= value <= after


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-12T10:00:00Z", datetime(2026, 10, 12, 10, tzinfo=UTC)),
        ("2026-10-12T10:00:00.123Z", datetime(2026, 10, 12, 10, 0, 0, 123000, tzinfo=UTC)),
        ("2026-10-12T12:00:00+02:00", datetime(2026, 10, 12, 10, tzinfo=UTC)),
        ("2026-10-12T10:00:00", datetime(2026, 10, 12, 10, tzinfo=UTC)),
        ("0001-01-01T00:00:00+01:00", None),
        ("9999-12-31T23:59:59-01:00", None),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_iso(raw, expected):
    assert parse_iso(raw) == expected
