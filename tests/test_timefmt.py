from datetime import UTC, datetime, timedelta, timezone

import pytest

from podpocket.timefmt import iso_z, pc_gmt


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
