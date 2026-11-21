"""Resolve manager-confirmed weekly hours; explicit dated records always win."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.routing.baseline import DrivingBaseline
from app.domain.errors import AppError
from app.models import CleanerAvailability, CleanerWeeklyHours
from app.workspaces import LEGACY_WORKSPACE_ID

NY = ZoneInfo("America/New_York")


def eastern_instant(day: date, civil: time) -> datetime:
    if civil.tzinfo is not None or civil.second or civil.microsecond:
        raise AppError("validation_error", "Use Eastern local hours and minutes.", 422)
    local = datetime.combine(day, civil, tzinfo=NY)
    if local.astimezone(UTC).astimezone(NY).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise AppError("validation_error", "That local time does not exist because of daylight saving time.", 422)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise AppError("validation_error", "That local time is ambiguous because of daylight saving time.", 422)
    return local.astimezone(UTC)


def weekly_capacity(weekly: CleanerWeeklyHours, day: date) -> CleanerAvailability | None:
    rule = next((rule for rule in weekly.days if int(rule["weekday"]) == day.weekday()), None)
    if rule is None:
        return None
    start = eastern_instant(day, time.fromisoformat(rule["start_time"]))
    end = eastern_instant(day, time.fromisoformat(rule["end_time"]))
    breaks = []
    if rule.get("break_start") and rule.get("break_end"):
        breaks = [
            {
                "start_at": eastern_instant(day, time.fromisoformat(rule["break_start"])).isoformat(),
                "end_at": eastern_instant(day, time.fromisoformat(rule["break_end"])).isoformat(),
            }
        ]
    return CleanerAvailability(
        id=weekly.cleaner_id,
        cleaner_id=weekly.cleaner_id,
        service_date=day,
        version=weekly.version,
        status="available",
        work_intervals=[{"start_at": start.isoformat(), "end_at": end.isoformat()}],
        unavailable_intervals=breaks,
        origin_label=rule["origin_label"],
        origin_source="weekly",
        assumed=False,
    )


def effective_availability(session: Session, day: date) -> list[CleanerAvailability]:
    dated = list(session.scalars(select(CleanerAvailability).where(CleanerAvailability.service_date == day)))
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID:
        return [row for row in dated if row.origin_source == "cleaner_opt_in"]
    explicit = {row.cleaner_id for row in dated}
    for weekly in session.scalars(select(CleanerWeeklyHours).order_by(CleanerWeeklyHours.cleaner_id)):
        if weekly.cleaner_id in explicit:
            continue
        try:
            capacity = weekly_capacity(weekly, day)
        except AppError:
            continue
        if capacity is not None:
            dated.append(capacity)
    return dated


def cleaner_ready_for_scheduling(session: Session, cleaner_id: uuid.UUID, day: date) -> bool:
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) == LEGACY_WORKSPACE_ID:
        return True
    weekly = session.scalar(select(CleanerWeeklyHours).where(CleanerWeeklyHours.cleaner_id == cleaner_id))
    baseline = session.info.get("driving_baseline")
    if weekly is None or not weekly.days or not weekly.starting_address_ciphertext:
        return False
    routes = weekly.origin_routes or {}
    return (
        isinstance(baseline, DrivingBaseline)
        and routes.get("status") == "ready"
        and routes.get("baseline_id") == baseline.digest
        and isinstance(routes.get("seconds"), dict)
        and any(
            row.cleaner_id == cleaner_id
            and row.status == "available"
            and bool(row.work_intervals)
            and row.origin_label == f"synthetic:start-{cleaner_id.hex}"
            and not row.assumed
            for row in effective_availability(session, day)
        )
    )
