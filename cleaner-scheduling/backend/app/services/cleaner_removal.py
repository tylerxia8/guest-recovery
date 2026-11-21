from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.errors import AppError
from app.models import Assignment, Cleaner, CleanerAccessLink, CleanerScheduleVersion, CleaningTask, ScheduleDay
from app.services.audit import write_audit
from app.services.schedules import _open_exception
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id


def remove_cleaner(
    session: Session, *, clock: Clock, cleaner_id: uuid.UUID, expected_version: int, actor: str
) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    cleaner = session.scalar(select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update())
    if cleaner is None:
        raise AppError("not_found", "Cleaner was not found.", 404)
    if cleaner.version != expected_version:
        raise AppError("stale_version", "Cleaner changed. Refresh the list before removing them.", 409)
    if not cleaner.active:
        return {"active": False, "released": 0, "needs_review": 0}
    now = clock.now()
    cleaner.active = False
    cleaner.version += 1
    cleaner.updated_at = now
    released = review = 0
    assignments = session.scalars(
        select(Assignment)
        .where(
            Assignment.cleaner_id == cleaner_id,
            Assignment.status.in_(("planned", "published", "acknowledged", "blocked")),
        )
        .with_for_update()
    ).all()
    for assignment in assignments:
        if assignment.completed_at is not None:
            continue
        if assignment.is_locked or assignment.planned_start <= now:
            assignment.status = "blocked"
            review += 1
            reason = "LOCK_CONFLICT" if assignment.is_locked else "IN_PROGRESS"
        else:
            assignment.status = "superseded"
            released += 1
            reason = "CLEANER_REMOVED"
            task = session.get(CleaningTask, assignment.task_id)
            if task is not None and task.status == "assigned":
                task.status = "ready"
                task.version += 1
                task.updated_at = now
        assignment.version += 1
        assignment.updated_at = now
        _open_exception(session, clock, str(assignment.task_id), reason)
    for version in session.scalars(
        select(CleanerScheduleVersion).where(
            CleanerScheduleVersion.cleaner_id == cleaner_id,
            CleanerScheduleVersion.status == "current",
        )
    ):
        version.status = "superseded"
    for link in session.scalars(
        select(CleanerAccessLink).where(
            CleanerAccessLink.cleaner_id == cleaner_id,
            CleanerAccessLink.revoked_at.is_(None),
        )
    ):
        link.revoked_at = now
    for day in session.scalars(select(ScheduleDay).with_for_update()):
        day.revision += 1
        day.updated_at = now
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="cleaner.removed",
        entity_type="cleaner",
        entity_id=cleaner_id,
        correlation_id=cleaner_id,
        reason="Manager removed cleaner from the active team.",
        redacted_changes={"active": False, "released": released, "needs_review": review},
        request_id=None,
    )
    session.flush()
    return {"active": False, "released": released, "needs_review": review}
