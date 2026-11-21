from __future__ import annotations

from datetime import UTC, date, time

from app.clock import FrozenClock, SystemClock
from app.domain.timeutil import business_local_to_utc


def test_system_clock_is_utc() -> None:
    instant = SystemClock().now()
    assert instant.tzinfo is not None
    assert instant.utcoffset() == UTC.utcoffset(instant)


def test_eastern_dispatch_instants_follow_dst() -> None:
    november = business_local_to_utc(date(2026, 11, 1), time(18, 0))
    march = business_local_to_utc(date(2026, 3, 8), time(18, 0))
    assert november.astimezone(UTC).hour == 23
    assert march.astimezone(UTC).hour == 22
    assert november.date() == date(2026, 11, 1)
    assert march.date() == date(2026, 3, 8)


def test_frozen_clock_rejects_naive_instants() -> None:
    import pytest

    with pytest.raises(ValueError):
        FrozenClock(business_local_to_utc(date(2026, 11, 1), time(18, 0)).replace(tzinfo=None))
