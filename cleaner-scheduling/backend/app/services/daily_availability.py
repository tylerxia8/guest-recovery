from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.errors import AppError
from app.models import Assignment, Cleaner, CleanerAvailability, CleanerWeeklyHours, ScheduleDay
from app.services.audit import write_audit
from app.services.weekly_availability import NY, weekly_capacity


def tomorrow_availability(session: Session, clock: Clock, cleaner: Cleaner) -> dict[str, object]:
    day = clock.now().astimezone(NY).date() + timedelta(days=1)
    row = session.scalar(
        select(CleanerAvailability).where(
            CleanerAvailability.cleaner_id == cleaner.id, CleanerAvailability.service_date == day
        )
    )
    status = row.status if row is not None and row.origin_source == "cleaner_opt_in" else "pending"
    assigned = session.scalar(
        select(Assignment.id)
        .where(
            Assignment.cleaner_id == cleaner.id,
            Assignment.service_date == day,
            Assignment.status.in_(("planned", "published", "acknowledged", "blocked", "completed")),
        )
        .limit(1)
    )
    return {
        "service_date": day.isoformat(),
        "status": status,
        "version": 0 if row is None else row.version,
        "can_change": status != "available" or assigned is None,
    }


def set_tomorrow_availability(
    session: Session,
    clock: Clock,
    cleaner: Cleaner,
    service_date: date,
    available: bool,
    expected_version: int,
    request_id: str,
) -> dict[str, object]:
    now = clock.now()
    if service_date != now.astimezone(NY).date() + timedelta(days=1):
        raise AppError("availability_date_changed", "Choose your availability for tomorrow in Eastern time.", 409)
    current = session.scalar(
        select(Cleaner).where(Cleaner.id == cleaner.id).with_for_update().execution_options(populate_existing=True)
    )
    if current is None or not current.active:
        raise AppError("link_unavailable", "This cleaner link is unavailable.", 401)
    row = session.scalar(
        select(CleanerAvailability)
        .where(CleanerAvailability.cleaner_id == cleaner.id, CleanerAvailability.service_date == service_date)
        .with_for_update()
    )
    if expected_version != (0 if row is None else row.version):
        raise AppError("stale_version", "Your availability changed. Refresh before saving.", 409)
    if row is not None and row.origin_source != "cleaner_opt_in" and row.status != "available":
        raise AppError(
            "working_hours_restricted", "Your manager restricted availability for this date. Contact your manager.", 409
        )
    status = "available" if available else "unavailable"
    if row is not None and row.origin_source == "cleaner_opt_in" and row.status == status:
        return {"availability": tomorrow_availability(session, clock, cleaner)}
    if not available:
        assigned = session.scalar(
            select(Assignment.id)
            .where(
                Assignment.cleaner_id == cleaner.id,
                Assignment.service_date == service_date,
                Assignment.status.in_(("planned", "published", "acknowledged", "blocked", "completed")),
            )
            .limit(1)
        )
        if assigned is not None:
            raise AppError(
                "assigned_work_exists", "You already have assigned work. Punt the houses or contact your manager.", 409
            )
    capacity = row
    if available:
        if capacity is None or (capacity.origin_source == "cleaner_opt_in" and not capacity.work_intervals):
            weekly = session.scalar(select(CleanerWeeklyHours).where(CleanerWeeklyHours.cleaner_id == cleaner.id))
            if weekly is not None:
                capacity = weekly_capacity(weekly, service_date)
        if capacity is None or not capacity.work_intervals or not capacity.origin_label:
            raise AppError(
                "working_hours_missing", "Set working hours for tomorrow before opting in. Contact your manager.", 409
            )
    if row is None:
        row = CleanerAvailability(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=cleaner.id,
            service_date=service_date,
            work_intervals=[],
            unavailable_intervals=[],
        )
        session.add(row)
    else:
        row.version += 1
        row.updated_at = now
    row.status = status
    if capacity is not None:
        row.work_intervals = capacity.work_intervals
        row.unavailable_intervals = capacity.unavailable_intervals
        row.origin_label = capacity.origin_label
        row.origin_ciphertext = capacity.origin_ciphertext
    row.origin_source = "cleaner_opt_in"
    row.assumed = False
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == service_date).with_for_update())
    if schedule_day is not None:
        schedule_day.revision += 1
        schedule_day.updated_at = now
    write_audit(
        session,
        clock=clock,
        actor_type="cleaner",
        actor_subject=f"cleaner:{cleaner.id}",
        action="cleaner.daily_availability_confirmed",
        entity_type="cleaner",
        entity_id=cleaner.id,
        correlation_id=uuid.UUID(request_id),
        reason=None,
        redacted_changes={"service_date": service_date.isoformat(), "available": available},
        request_id=request_id,
    )
    session.flush()
    return {"availability": tomorrow_availability(session, clock, cleaner)}
