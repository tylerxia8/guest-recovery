from __future__ import annotations

from datetime import UTC, datetime, timedelta


def turnover_deadline(release: datetime, arrival_deadline: datetime | None = None) -> datetime:
    """48 elapsed hours after checkout, capped by the next arrival's readiness limit."""
    cap = release.astimezone(UTC) + timedelta(hours=48)
    return min(cap, arrival_deadline) if arrival_deadline is not None else cap


def effective_deadline(release: datetime, deadline: datetime | None, target: datetime | None = None) -> datetime:
    cap = turnover_deadline(release, deadline)
    return min(cap, target) if target is not None else cap


def readiness_deadline(next_checkin: datetime, buffer_minutes: int) -> datetime:
    """Subtract the supplied buffer from the next check-in. Does not invent a buffer."""
    if buffer_minutes < 1:
        raise ValueError("readiness buffer must be positive")
    return next_checkin - timedelta(minutes=buffer_minutes)


DEFAULT_CLEANING_MINUTES = 120


def resolve_duration(bedrooms: int, override_minutes: int | None) -> tuple[int, str]:
    if override_minutes is not None:
        if override_minutes < 1:
            raise ValueError("Cleaning duration must be positive.")
        return override_minutes, "property_override"
    if bedrooms < 0:
        raise ValueError("Bedroom count cannot be negative.")
    return 60 + 30 * bedrooms, "bedroom_default"


def eligible_for_future_dispatch(
    *,
    status: str,
    duration_minutes: int | None,
    blocking_exception_open: bool,
) -> bool:
    """Deadline-less, undurationed, impossible, canceled, or completed work is not dispatchable.

    Returning true does not publish a schedule. M3 has no dispatcher.
    """
    if status != "ready":
        return False
    if duration_minutes is None or duration_minutes < 1:
        return False
    return not blocking_exception_open
