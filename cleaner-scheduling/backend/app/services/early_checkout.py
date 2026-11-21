from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.scheduling import solve, validate
from app.domain.tasks import effective_deadline
from app.models import Assignment, CleaningTask, OutboxEvent, Reservation
from app.services.audit import write_audit
from app.services.checkout_policy import baseline_checkout
from app.services.local_routing import current_local_baseline
from app.services.reservation_sync import publication_blocked
from app.services.schedule_freshness import bump_task
from app.services.schedules import _inputs, assert_manual_publication, override_assignment
from app.services.task_generation import _open_task_exception, recompute_property_tasks
from app.services.weekly_availability import NY
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id


def confirm_early_checkout(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    reason: str,
    actor: str,
    request_id: str,
) -> tuple[CleaningTask, str]:
    reservation = session.scalar(
        select(Reservation)
        .join(CleaningTask, CleaningTask.source_reservation_id == Reservation.id)
        .where(CleaningTask.id == task_id)
    )
    if reservation is not None and reservation.checkout_at.astimezone(NY).date() != clock.now().astimezone(NY).date():
        raise AppError("checkout_unavailable", "Confirm departure only on the current scheduled checkout day.", 409)
    return _confirm_checkout(
        session,
        settings=settings,
        clock=clock,
        task_id=task_id,
        expected_version=expected_version,
        confirmed_at=clock.now(),
        reason=reason,
        actor=actor,
        request_id=request_id,
    )


def confirm_message_checkout(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    departed_at: datetime,
    reason: str,
    actor: str,
    request_id: str,
) -> tuple[CleaningTask, str]:
    return _confirm_checkout(
        session,
        settings=settings,
        clock=clock,
        task_id=task_id,
        expected_version=expected_version,
        confirmed_at=departed_at,
        reason=reason,
        actor=actor,
        request_id=request_id,
    )


def _confirm_checkout(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    confirmed_at: datetime,
    reason: str,
    actor: str,
    request_id: str,
) -> tuple[CleaningTask, str]:
    if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
        raise AppError("validation_error", "Departure time must include a timezone.", 422)
    confirmed_at = confirmed_at.astimezone(UTC)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    task = session.scalar(select(CleaningTask).where(CleaningTask.id == task_id).with_for_update())
    if task is None:
        raise AppError("not_found", "Cleaning task was not found.", 404)
    if task.version != expected_version:
        raise AppError("stale_version", "Task changed. Refresh before confirming departure.", 409)
    if task.status in {"completed", "canceled"} or task.confirmed_checkout_at is not None:
        raise AppError("checkout_unavailable", "This task cannot receive another departure confirmation.", 409)
    if publication_blocked(session, clock=clock, settings=settings):
        raise AppError("stale_source", "Refresh reservation imports before confirming departure.", 409)
    reservation = session.scalar(
        select(Reservation).where(Reservation.id == task.source_reservation_id).with_for_update()
    )
    now = clock.now().astimezone(UTC)
    if (
        reservation is None
        or reservation.property_id != task.property_id
        or reservation.status != "active"
        or not reservation.checkin_at <= confirmed_at <= now < baseline_checkout(session, reservation)
    ):
        raise AppError("checkout_unavailable", "Confirm departure only on the current scheduled checkout day.", 409)
    assignments = list(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.task_id == task.id,
                Assignment.status.in_(("planned", "published", "acknowledged", "blocked")),
            )
            .with_for_update()
        )
    )
    deadline = effective_deadline(confirmed_at, task.deadline_at, task.manager_target_at)
    if any(row.planned_end > deadline for row in assignments):
        raise AppError(
            "assignment_review_required", "Review the assigned cleaning deadline before confirming departure.", 409
        )
    task.confirmed_checkout_at = confirmed_at
    task.confirmed_checkout_reservation_version = reservation.version
    recompute_property_tasks(session, clock=clock, property_id=task.property_id)
    result = "ready_for_earlier_allocation"
    if assignments:
        if len(assignments) != 1:
            raise AppError("assignment_review_required", "Multiple active assignments require manager review.", 409)
        result = _retime_assignment(session, settings, clock, task, assignments[0], actor, reason)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="cleaning_task.checkout_confirmed",
        entity_type="cleaning_task",
        entity_id=task.id,
        correlation_id=uuid.UUID(request_id),
        reason=reason,
        redacted_changes={
            "confirmed_checkout_at": confirmed_at.isoformat(),
            "reservation_version": reservation.version,
            "result": result,
        },
        request_id=request_id,
    )
    session.flush()
    return task, result


def invalidate_message_checkout(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    reason: str,
    actor: str,
    request_id: str,
) -> CleaningTask:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    task = session.scalar(select(CleaningTask).where(CleaningTask.id == task_id).with_for_update())
    if task is None:
        raise AppError("not_found", "Cleaning task was not found.", 404)
    if task.version != expected_version:
        raise AppError("stale_version", "Task changed. Refresh before reviewing departure.", 409)
    if task.status in {"completed", "canceled"} or task.confirmed_checkout_at is None:
        raise AppError("checkout_unavailable", "This departure confirmation cannot be withdrawn.", 409)
    task.confirmed_checkout_at = None
    task.confirmed_checkout_reservation_version = None
    recompute_property_tasks(session, clock=clock, property_id=task.property_id)
    return review_message_checkout(
        session,
        settings=settings,
        clock=clock,
        task_id=task.id,
        expected_version=task.version,
        reason=reason,
        actor=actor,
        request_id=request_id,
    )


def review_message_checkout(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    reason: str,
    actor: str,
    request_id: str,
) -> CleaningTask:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    task = session.scalar(select(CleaningTask).where(CleaningTask.id == task_id).with_for_update())
    if task is None:
        raise AppError("not_found", "Cleaning task was not found.", 404)
    if task.version != expected_version:
        raise AppError("stale_version", "Task changed. Refresh before reviewing departure.", 409)
    if task.status in {"completed", "canceled"}:
        raise AppError("checkout_unavailable", "This cleaning task cannot receive a departure review.", 409)
    _open_task_exception(
        session,
        clock,
        code="GUEST_DEPARTURE_REVIEW",
        task_id=task.id,
        dedupe_key=f"guest-departure-review:{task.id}",
        details={},
        severity="critical",
    )
    now = clock.now()
    task.version += 1
    task.updated_at = now
    bump_task(session, task)
    for assignment in session.scalars(
        select(Assignment)
        .where(
            Assignment.task_id == task.id,
            Assignment.status.in_(("planned", "published", "acknowledged", "blocked")),
        )
        .with_for_update()
    ):
        if assignment.status == "blocked":
            continue
        assignment.status = "blocked"
        assignment.version += 1
        assignment.updated_at = now
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                topic="cleaning.reservation_changed",
                aggregate_id=assignment.id,
                aggregate_version=assignment.version,
                intent_key=f"departure-review:{assignment.id}:{assignment.version}",
                payload={
                    "assignment_id": str(assignment.id),
                    "cleaner_id": str(assignment.cleaner_id),
                    "task_id": str(task.id),
                    "service_date": assignment.service_date.isoformat(),
                    "change": "review",
                    "task_version": task.version,
                },
                available_at=now,
                claimed_until=None,
                processed_at=None,
                attempt_count=0,
                last_error_code=None,
            )
        )
    write_audit(
        session,
        clock=clock,
        actor_type="system",
        actor_subject=actor,
        action="cleaning_task.checkout_review_required",
        entity_type="cleaning_task",
        entity_id=task.id,
        correlation_id=uuid.UUID(request_id),
        reason=reason,
        redacted_changes={"result": "manager_review_required"},
        request_id=request_id,
    )
    session.flush()
    return task


def _retime_assignment(
    session: Session,
    settings: Settings,
    clock: Clock,
    task: CleaningTask,
    assignment: Assignment,
    actor: str,
    reason: str,
) -> str:
    try:
        assert_manual_publication(settings)
    except AppError:
        return "existing_assignment_preserved_scheduling_paused"
    if (
        assignment.is_locked
        or assignment.status not in {"planned", "published", "acknowledged"}
        or assignment.planned_start <= clock.now()
        or assignment.service_date != clock.now().astimezone(NY).date()
    ):
        return "existing_assignment_preserved"
    if settings.hospitable_read_only and current_local_baseline(session, settings) is None:
        return "existing_assignment_preserved_routes_unavailable"
    others = tuple(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.cleaner_id == assignment.cleaner_id,
                Assignment.service_date == assignment.service_date,
                Assignment.id != assignment.id,
                Assignment.status.in_(("planned", "published", "acknowledged", "blocked", "completed")),
            )
            .with_for_update()
        )
    )
    jobs, shifts, travel, _snapshot, _extra = _inputs(session, clock, assignment.service_date, others)
    identities = {str(row.task_id) for row in others} | {str(task.id)}
    jobs = [job for job in jobs if job.task_id in identities]
    shifts = [shift for shift in shifts if shift.cleaner_id == str(assignment.cleaner_id)]
    if {job.task_id for job in jobs} != identities or not shifts:
        return "existing_assignment_preserved_no_earlier_slot"
    result = solve(jobs, shifts, travel, time_limit_seconds=2)
    if validate(result, jobs, shifts, travel) or {item.task_id for item in result.placed} != identities:
        return "existing_assignment_preserved_no_earlier_slot"
    chosen = next(item for item in result.placed if item.task_id == str(task.id))
    if chosen.start >= assignment.planned_start:
        return "existing_assignment_preserved_no_earlier_slot"
    replacement = override_assignment(
        session,
        clock=clock,
        assignment_id=assignment.id,
        expected_version=assignment.version,
        cleaner_id=assignment.cleaner_id,
        planned_start=chosen.start,
        reason=reason,
        actor=actor,
    )
    placed_by_task = {item.task_id: item for item in result.placed}
    for other in others:
        if other.status == "completed" or other.planned_start <= clock.now():
            continue
        seconds = int(placed_by_task[str(other.task_id)].travel_before.total_seconds())
        if other.travel_before_seconds == seconds:
            continue
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=actor,
            action="assignment.route_updated",
            entity_type="assignment",
            entity_id=other.id,
            correlation_id=replacement.id,
            reason="confirmed early departure",
            redacted_changes={"previous_seconds": other.travel_before_seconds, "seconds": seconds},
            request_id=None,
        )
        other.travel_before_seconds = seconds
        other.version += 1
        other.updated_at = clock.now()
    return "same_cleaner_moved_earlier"
