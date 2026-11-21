from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.tasks import eligible_for_future_dispatch, readiness_deadline, resolve_duration, turnover_deadline
from app.models import (
    Assignment,
    CleaningOffer,
    CleaningTask,
    OperationalException,
    OutboxEvent,
    PolicyVersion,
    Property,
    PropertyInstruction,
    Reservation,
)
from app.services.audit import write_audit
from app.services.checkout_policy import baseline_checkout
from app.services.instructions import latest_instruction
from app.services.schedule_freshness import NY, bump_task

_TASK_FIELDS = (
    "release_at",
    "deadline_at",
    "next_reservation_id",
    "duration_minutes",
    "duration_source",
    "status",
    "manager_target_at",
)


def active_policy_settings(session: Session) -> dict[str, object] | None:
    policy = session.scalar(
        select(PolicyVersion).where(PolicyVersion.is_active.is_(True), PolicyVersion.approved_at.is_not(None)).limit(1)
    )
    if policy is None:
        return None
    return dict(policy.settings)


def recompute_property_tasks(session: Session, *, clock: Clock, property_id: uuid.UUID) -> list[CleaningTask]:
    prop = session.get(Property, property_id)
    if prop is None:
        return []
    settings = active_policy_settings(session)
    buffer = _buffer(settings)
    reservations = list(session.scalars(select(Reservation).where(Reservation.property_id == property_id)).all())
    active = [row for row in reservations if row.status == "active"]
    instruction = latest_instruction(session, property_id)
    updated: list[CleaningTask] = []
    for reservation in reservations:
        task = _upsert_task(
            session,
            clock=clock,
            prop=prop,
            reservation=reservation,
            active=active,
            buffer=buffer,
            instruction=instruction,
        )
        if task is not None:
            updated.append(task)
    return updated


def set_manager_target(
    session: Session,
    *,
    clock: Clock,
    task_id: uuid.UUID,
    expected_version: int,
    manager_target_at: datetime | None,
    reason: str,
    actor: str,
) -> CleaningTask:
    from app.domain.errors import AppError

    task = session.scalar(select(CleaningTask).where(CleaningTask.id == task_id).with_for_update())
    if task is None:
        raise AppError("not_found", "Cleaning task was not found.", 404)
    if task.version != expected_version:
        raise AppError("stale_version", "Task version is stale.", 409)
    if task.status in {"completed", "canceled"}:
        raise AppError("terminal_task", "Completed or canceled tasks are not retargeted.", 409)
    if manager_target_at is not None:
        if manager_target_at.tzinfo is None:
            raise AppError("validation_error", "manager_target_at must include an offset.", 422)
        if manager_target_at <= task.release_at:
            raise AppError("validation_error", "manager_target_at must be after release.", 422)
        if manager_target_at > turnover_deadline(task.release_at, task.deadline_at):
            raise AppError("deadline_exists", "A target cannot extend the cleaning deadline.", 409)
        task.manager_target_at = manager_target_at.astimezone(clock.now().tzinfo)
        task.status = "ready"
    else:
        task.manager_target_at = None
        task.status = "ready"
    task.version += 1
    task.updated_at = clock.now()
    _revise(session, clock, task, instruction=latest_instruction(session, task.property_id), reason=reason, actor=actor)
    session.flush()
    return task


def departure_requires_review(session: Session, task_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(OperationalException.id)
            .where(
                OperationalException.task_id == task_id,
                OperationalException.code == "GUEST_DEPARTURE_REVIEW",
                OperationalException.status.in_(("open", "acknowledged")),
            )
            .limit(1)
        )
        is not None
    )


def task_dispatch_allowed(session: Session, task: CleaningTask) -> bool:
    blocking = session.scalar(
        select(OperationalException.id).where(
            OperationalException.status.in_(("open", "acknowledged")),
            OperationalException.code.in_(
                ("DEADLINE_IMPOSSIBLE", "MISSING_DURATION", "MISSING_READINESS_BUFFER", "GUEST_DEPARTURE_REVIEW")
            ),
            OperationalException.dedupe_key.like(f"%{task.id}%"),
        )
    )
    return eligible_for_future_dispatch(
        status=task.status,
        duration_minutes=task.duration_minutes,
        blocking_exception_open=blocking is not None,
    )


def _upsert_task(
    session: Session,
    *,
    clock: Clock,
    prop: Property,
    reservation: Reservation,
    active: list[Reservation],
    buffer: int | None,
    instruction: PropertyInstruction | None,
) -> CleaningTask | None:
    existing = session.scalar(
        select(CleaningTask).where(
            CleaningTask.source_reservation_id == reservation.id,
            CleaningTask.kind == "departure",
        )
    )
    if existing is not None and existing.status == "completed":
        return existing
    if reservation.status != "active":
        if existing is None or existing.status == "completed":
            return existing
        if existing.status != "canceled":
            existing.confirmed_checkout_at = None
            existing.confirmed_checkout_reservation_version = None
            existing.status = "canceled"
            existing.version += 1
            existing.updated_at = clock.now()
            _close_exceptions(session, clock, existing.id, "source_canceled")
            _revise(session, clock, existing, instruction, "source reservation canceled", "reservation-sync")
        return existing
    if existing is not None and existing.status == "canceled":
        _open_task_exception(
            session,
            clock,
            code="TASK_REQUIRES_RESTORATION",
            task_id=existing.id,
            dedupe_key=f"restore:{existing.id}",
            details={"source_reservation_id": str(reservation.id)},
            severity="warning",
        )
        return existing
    nxt = _next_arrival(reservation, active)
    deadline = None
    if nxt is not None and buffer is not None:
        deadline = readiness_deadline(nxt.checkin_at, buffer)
    elif nxt is not None:
        deadline = nxt.checkin_at
    confirmed = (
        existing.confirmed_checkout_at
        if existing is not None and existing.confirmed_checkout_reservation_version == reservation.version
        else None
    )
    release = confirmed or baseline_checkout(session, reservation)
    deadline = turnover_deadline(release, deadline)
    duration, duration_source = resolve_duration(prop.bedrooms, prop.duration_override_minutes)
    status = _status(existing, deadline)
    now = clock.now()
    if existing is None:
        existing = CleaningTask(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            property_id=prop.id,
            source_reservation_id=reservation.id,
            kind="departure",
            next_reservation_id=None if nxt is None else nxt.id,
            release_at=release,
            deadline_at=deadline,
            manager_target_at=None,
            duration_minutes=duration,
            duration_source=duration_source,
            status=status,
            completed_at=None,
            completion_actor=None,
        )
        session.add(existing)
        session.flush()
        _sync_exceptions(session, clock, existing, nxt is not None, buffer, deadline)
        _revise(session, clock, existing, instruction, "task derived", "reservation-sync")
        return existing
    changed = _assign_if_changed(
        existing,
        release_at=release,
        confirmed_checkout_at=confirmed,
        confirmed_checkout_reservation_version=reservation.version if confirmed is not None else None,
        deadline_at=deadline,
        next_reservation_id=None if nxt is None else nxt.id,
        duration_minutes=duration,
        duration_source=duration_source,
        status=existing.status if existing.status == "completed" else status,
    )
    _sync_exceptions(session, clock, existing, nxt is not None, buffer, deadline)
    if changed:
        existing.version += 1
        existing.updated_at = now
        _revise(session, clock, existing, instruction, "task revised", "reservation-sync")
    return existing


def _status(existing: CleaningTask | None, deadline: datetime | None) -> str:
    if existing is not None and existing.status in {"completed", "assigned"}:
        return existing.status
    if deadline is not None or (existing is not None and existing.manager_target_at is not None):
        return "ready"
    return "pending_deadline"


def _assign_if_changed(task: CleaningTask, **values: object) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(task, name) != value:
            setattr(task, name, value)
            changed = True
    return changed


def _next_arrival(reservation: Reservation, active: list[Reservation]) -> Reservation | None:
    candidates = [row for row in active if row.id != reservation.id and row.checkin_at >= reservation.checkout_at]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row.checkin_at, str(row.id)))


def _sync_exceptions(
    session: Session,
    clock: Clock,
    task: CleaningTask,
    has_next: bool,
    buffer: int | None,
    deadline: datetime | None,
) -> None:
    if task.duration_minutes is None:
        _open_task_exception(
            session,
            clock,
            code="MISSING_DURATION",
            task_id=task.id,
            dedupe_key=f"missing-duration:{task.id}",
            details={"duration_source": task.duration_source},
            severity="warning",
        )
    else:
        _close_code(session, clock, f"missing-duration:{task.id}", "duration_configured")
    if has_next and buffer is None:
        _open_task_exception(
            session,
            clock,
            code="MISSING_READINESS_BUFFER",
            task_id=task.id,
            dedupe_key=f"missing-buffer:{task.id}",
            details={"reason": "no approved readiness buffer"},
            severity="warning",
        )
    else:
        _close_code(session, clock, f"missing-buffer:{task.id}", "buffer_not_required")
    if deadline is not None and deadline <= task.release_at:
        _open_task_exception(
            session,
            clock,
            code="DEADLINE_IMPOSSIBLE",
            task_id=task.id,
            dedupe_key=f"deadline-impossible:{task.id}",
            details={"release_at": task.release_at.isoformat(), "deadline_at": deadline.isoformat()},
            severity="critical",
        )
    else:
        _close_code(session, clock, f"deadline-impossible:{task.id}", "window_possible")


def _revise(
    session: Session,
    clock: Clock,
    task: CleaningTask,
    instruction: PropertyInstruction | None,
    reason: str,
    actor: str,
) -> None:
    now = clock.now()
    if actor == "reservation-sync":
        for offer in session.scalars(
            select(CleaningOffer)
            .where(CleaningOffer.task_id == task.id, CleaningOffer.status.in_(("open", "expired")))
            .with_for_update()
        ):
            deadline = turnover_deadline(task.release_at, task.deadline_at)
            if task.status != "canceled" and (
                task.release_at.astimezone(NY).date() <= offer.service_date <= deadline.astimezone(NY).date()
            ):
                continue
            offer.status = "canceled"
            offer.version += 1
            offer.updated_at = now
            for problem in session.scalars(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"cleaner-offer-unclaimed:{offer.id}",
                    OperationalException.status.in_(("open", "acknowledged")),
                )
            ):
                problem.status = "resolved"
                problem.resolved_at = problem.updated_at = now
                problem.resolution_reason = "The booking change withdrew this cleaning offer."
                problem.version += 1
            write_audit(
                session,
                clock=clock,
                actor_type="system",
                actor_subject=actor,
                action="cleaning_offer.source_changed",
                entity_type="cleaning_offer",
                entity_id=offer.id,
                correlation_id=task.id,
                reason="Booking change invalidated the offered cleaning date.",
                redacted_changes={"status": "canceled"},
                request_id=None,
            )
        for assignment in session.scalars(
            select(Assignment)
            .where(
                Assignment.task_id == task.id,
                Assignment.status.in_(("planned", "published", "acknowledged", "blocked")),
            )
            .with_for_update()
        ):
            deadline = turnover_deadline(task.release_at, task.deadline_at)
            if task.manager_target_at is not None:
                deadline = min(deadline, task.manager_target_at)
            if task.status != "canceled" and (
                assignment.planned_start >= task.release_at
                and assignment.planned_end <= deadline
                and assignment.duration_minutes == task.duration_minutes
            ):
                continue
            protected = assignment.is_locked or assignment.planned_start <= now
            assignment.status = "blocked" if protected else "canceled"
            assignment.version += 1
            assignment.updated_at = now
            if protected:
                _open_task_exception(
                    session,
                    clock,
                    code="RESERVATION_CHANGE_REVIEW",
                    task_id=task.id,
                    dedupe_key=f"reservation-change:{assignment.id}",
                    details={"assignment_id": str(assignment.id)},
                    severity="warning",
                )
            elif task.status == "assigned":
                task.status = "ready"
            session.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    topic="cleaning.reservation_changed",
                    aggregate_id=assignment.id,
                    aggregate_version=assignment.version,
                    intent_key=f"reservation-change:{assignment.id}:{task.version}",
                    payload={
                        "assignment_id": str(assignment.id),
                        "cleaner_id": str(assignment.cleaner_id),
                        "task_id": str(task.id),
                        "service_date": assignment.service_date.isoformat(),
                        "change": "review" if protected else "removed",
                        "task_version": task.version,
                    },
                    available_at=now,
                    claimed_until=None,
                    processed_at=None,
                    attempt_count=0,
                    last_error_code=None,
                )
            )
    payload = {
        "task_id": str(task.id),
        "version": task.version,
        "status": task.status,
        "release_at": task.release_at.isoformat(),
        "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
        "manager_target_at": task.manager_target_at.isoformat() if task.manager_target_at else None,
        "duration_minutes": task.duration_minutes,
        "duration_source": task.duration_source,
        "instruction_id": None if instruction is None else str(instruction.id),
        "instruction_version": None if instruction is None else instruction.instruction_version,
    }
    session.add(
        OutboxEvent(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            topic="task.revised",
            aggregate_id=task.id,
            aggregate_version=task.version,
            intent_key=f"task:{task.id}:{task.version}",
            payload=payload,
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
        actor_type="system" if actor == "reservation-sync" else "operator",
        actor_subject=actor,
        action="cleaning_task.revised",
        entity_type="cleaning_task",
        entity_id=task.id,
        correlation_id=task.id,
        reason=reason,
        redacted_changes={"status": task.status, "version": task.version, "duration_source": task.duration_source},
        request_id=None,
    )
    bump_task(session, task)


def _open_task_exception(
    session: Session,
    clock: Clock,
    *,
    code: str,
    task_id: uuid.UUID,
    dedupe_key: str,
    details: dict[str, object],
    severity: str,
) -> None:
    existing = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == dedupe_key,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if existing is not None:
        return
    now = clock.now()
    session.add(
        OperationalException(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            code=code,
            severity=severity,
            dedupe_key=dedupe_key,
            status="open",
            task_id=task_id,
            assignment_id=None,
            sync_run_id=None,
            owner_subject=None,
            details=details,
            opened_at=now,
            resolved_at=None,
            resolution_reason=None,
        )
    )


def _close_code(session: Session, clock: Clock, dedupe_key: str, reason: str) -> None:
    row = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == dedupe_key,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if row is None:
        return
    row.status = "resolved"
    row.resolved_at = clock.now()
    row.resolution_reason = reason
    row.version += 1
    row.updated_at = clock.now()


def _close_exceptions(session: Session, clock: Clock, task_id: uuid.UUID, reason: str) -> None:
    rows = session.scalars(
        select(OperationalException).where(
            OperationalException.task_id == task_id,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    ).all()
    now = clock.now()
    for row in rows:
        row.status = "resolved"
        row.resolved_at = now
        row.resolution_reason = reason
        row.version += 1
        row.updated_at = now


def _buffer(settings: dict[str, object] | None) -> int | None:
    if settings is None:
        return None
    value = settings.get("readiness_buffer_minutes")
    if isinstance(value, int) and value > 0:
        return value
    return None
