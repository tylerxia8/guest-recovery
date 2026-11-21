from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

BUSINESS_ZONE = ZoneInfo("America/New_York")


def business_local_to_utc(civil_date: date, civil_time: time) -> datetime:
    """Convert a business-local civil time to UTC. The time must be naive."""
    if civil_time.tzinfo is not None:
        raise ValueError("civil time must be naive")
    local = datetime.combine(civil_date, civil_time, tzinfo=BUSINESS_ZONE)
    return local.astimezone(UTC)


def dump_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds")
