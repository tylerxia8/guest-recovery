from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, CleaningTask, ScheduleDay

NY = ZoneInfo("America/New_York")
_LIVE = ("planned", "published", "acknowledged", "blocked", "completed")


def bump_task(session: Session, task: CleaningTask) -> None:
    _bump(session, task.release_at.astimezone(NY).date(), task.updated_at)
    for day in session.scalars(select(ScheduleDay)).all():
        if day.service_date != task.release_at.astimezone(NY).date():
            day.revision += 1
            day.updated_at = task.updated_at


def bump_property(session: Session, property_id: uuid.UUID, now: datetime) -> None:
    tasks = session.scalars(select(CleaningTask).where(CleaningTask.property_id == property_id)).all()
    dates = {task.release_at.astimezone(NY).date() for task in tasks}
    for service_date in dates:
        _bump(session, service_date, now)


def cancel_unstarted_assignments(session: Session, task: CleaningTask, now: datetime) -> None:
    rows = session.scalars(select(Assignment).where(Assignment.task_id == task.id, Assignment.status.in_(_LIVE))).all()
    for row in rows:
        if row.status == "completed" or row.completed_at is not None:
            continue
        row.status = "canceled"
        row.version += 1
        row.updated_at = now


def _bump(session: Session, service_date: date, now: datetime) -> None:
    day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == service_date))
    if day is None:
        session.add(
            ScheduleDay(
                id=uuid.uuid4(),
                created_at=now.astimezone(UTC),
                updated_at=now.astimezone(UTC),
                service_date=service_date,
                active_run_id=None,
                revision=1,
                policy_version=1,
            )
        )
        session.flush()
        return
    day.revision += 1
    day.updated_at = now.astimezone(UTC)
