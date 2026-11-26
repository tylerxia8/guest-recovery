"""Trusted, workspace-scoped commands for the co-located guest inbox."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock, SystemClock
from app.config import Settings
from app.models.tables import Assignment, Cleaner, CleaningTask, EmailDelivery, Property, Reservation
from app.services.field_cipher import reveal_text
from app.services.guest_stay_policy import assess
from app.services.instructions import latest_instruction
from app.services.task_generation import _revise
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Times must include a timezone.")
    return result.astimezone(UTC)


def _reservation(session: Session, body: dict[str, Any]) -> Reservation:
    row = session.scalar(
        select(Reservation)
        .join(Property, Property.id == Reservation.property_id)
        .where(
            Reservation.provider == "hospitable",
            Reservation.external_id == body["reservation_external_id"],
            Reservation.status == "active",
            Property.provider == "hospitable",
            Property.external_id == body["property_external_id"],
            Property.active.is_(True),
        )
    )
    if row is None:
        raise ValueError("Refresh the scheduler until this property and reservation are available.")
    return row


def _touchup_id(session: Session, identifier: str) -> UUID:
    return uuid5(get_workspace_id(session), f"guest-touchup:{UUID(identifier)}")


def _find_touchup(session: Session, identifier: str, *, lock: bool = False) -> CleaningTask | None:
    task = session.get(CleaningTask, _touchup_id(session, identifier), with_for_update=lock)
    if task is not None:
        return task
    query = select(CleaningTask).where(
        CleaningTask.external_return_request_id == UUID(identifier), CleaningTask.kind == "guest_touchup"
    )
    return session.scalar(query.with_for_update() if lock else query)


def _assignment_email_status(session: Session, assignment: Assignment, settings: Settings) -> str | None:
    deliveries = session.scalars(
        select(EmailDelivery)
        .where(
            EmailDelivery.cleaner_id == assignment.cleaner_id,
            EmailDelivery.cleaner_schedule_version_id == assignment.cleaner_schedule_version_id,
            EmailDelivery.service_date == assignment.service_date,
            EmailDelivery.kind == "assignment",
        )
        .order_by(EmailDelivery.created_at.desc(), EmailDelivery.id.desc())
        .limit(20)
    )
    for delivery in deliveries:
        try:
            snapshot = json.loads(reveal_text(delivery.snapshot_ciphertext, settings.field_encryption_secret))
        except (InvalidTag, ValueError, TypeError):
            continue
        if isinstance(snapshot, dict):
            identifiers = snapshot.get("assignment_ids")
            if (
                snapshot.get("notification") != "reservation_change"
                and isinstance(identifiers, list)
                and str(assignment.id) in identifiers
            ):
                return delivery.status
    return None


def touchup_status(
    session: Session, identifier: str, *, reservation_id: UUID | None = None, settings: Settings | None = None
) -> dict[str, object]:
    task = _find_touchup(session, identifier)
    if not task or task.kind != "guest_touchup" or (reservation_id and task.source_reservation_id != reservation_id):
        raise LookupError(identifier)
    result: dict[str, object] = {
        "task_id": str(task.id),
        "status": "needs_manager",
        "proposal_failure": "Review and schedule this touch-up in Cleaner Scheduling.",
    }
    if task.status in {"completed", "canceled"}:
        result.update(status="completed" if task.status == "completed" else "cancelled", proposal_failure=None)
    elif task.status == "assigned":
        assignment = session.scalar(
            select(Assignment)
            .where(Assignment.task_id == task.id, Assignment.status.in_(("published", "acknowledged")))
            .order_by(Assignment.assignment_version.desc())
            .limit(1)
        )
        if assignment:
            cleaner = session.get(Cleaner, assignment.cleaner_id)
            result.update(
                status="accepted" if assignment.status == "acknowledged" else "scheduled",
                proposal_failure=None,
                assignment={
                    "cleaner_name": cleaner.display_name if cleaner else None,
                    "start_at": assignment.planned_start.isoformat(),
                    "end_at": assignment.planned_end.isoformat(),
                },
            )
            if settings is not None:
                result["schedule_email_status"] = _assignment_email_status(session, assignment, settings)
    return result


def _create_touchup(session: Session, body: dict[str, Any], clock: Clock) -> dict[str, object]:
    identifier = body["external_request_id"]
    key = _touchup_id(session, identifier)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    reservation = _reservation(session, body)
    start, end = _time(body["release_at"]), _time(body["deadline_at"])
    scope = body["scope"].strip()
    duration = body["duration_minutes"]
    existing = _find_touchup(session, identifier, lock=True)
    if existing:
        previous = (
            existing.source_reservation_id,
            existing.release_at,
            existing.deadline_at,
            existing.duration_minutes,
            existing.guest_scope,
        )
        if previous != (reservation.id, start, end, duration, scope):
            raise ValueError("This touch-up was already created with different details.")
        return touchup_status(session, identifier)
    now = clock.now()
    if (
        body.get("guest_access_confirmed") is not True
        or not 1 <= len(scope) <= 1000
        or type(duration) is not int
        or not 15 <= duration <= 240
        or not reservation.checkin_at <= start < end <= reservation.checkout_at
        or not now < start < end <= now + timedelta(hours=48)
        or end - start < timedelta(minutes=duration)
    ):
        raise ValueError("The touch-up must fit the guest's confirmed entry window and current stay.")
    session.add(
        CleaningTask(
            id=key,
            created_at=now,
            updated_at=now,
            property_id=reservation.property_id,
            source_reservation_id=reservation.id,
            kind="guest_touchup",
            guest_scope=scope,
            release_at=start,
            deadline_at=end,
            duration_minutes=duration,
            duration_source="property_override",
            status="ready",
        )
    )
    session.flush()
    return touchup_status(session, identifier)


def _withdraw_touchup(session: Session, identifier: str, clock: Clock) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    task = _find_touchup(session, identifier, lock=True)
    if task is None or task.kind != "guest_touchup":
        raise LookupError(identifier)
    if task.status not in {"completed", "canceled"}:
        task.status = "canceled"
        task.updated_at = clock.now()
        task.version += 1
        _revise(
            session,
            clock,
            task,
            latest_instruction(session, task.property_id),
            "Guest entry permission changed",
            "reservation-sync",
        )
        session.flush()
    return touchup_status(session, identifier)


def handle(
    session: Session, method: str, path: str, body: dict[str, Any] | None, *, clock: Clock | None = None
) -> dict[str, object]:
    if method == "POST" and body is not None:
        clock = clock or SystemClock()
        if path.startswith("/v1/cleaner-returns/") and path.endswith("/withdraw"):
            return _withdraw_touchup(session, path.split("/")[-2], clock)
        if path == "/v1/cleaner-returns":
            return _create_touchup(session, body, clock)
        if path == "/v1/stay-adjustments":
            acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
            reservation = _reservation(session, body)
            if (
                _time(body["booked_check_in"]) != reservation.checkin_at
                or _time(body["booked_check_out"]) != reservation.checkout_at
            ):
                raise ValueError("The booking times changed. Refresh both reservation sources before confirming.")
            return assess(session, reservation, body["request_id"], body["kind"], _time(body["target_at"]), clock.now())
    if method == "GET" and path.startswith("/v1/cleaner-returns/"):
        return touchup_status(session, path.rsplit("/", 1)[1])
    raise ValueError("Use Cleaner Scheduling to review this operation.")
