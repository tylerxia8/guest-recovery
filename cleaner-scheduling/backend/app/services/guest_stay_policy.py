"""Approve stay changes against the scheduler's current occupancy and work."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import FrozenClock
from app.config import get_settings
from app.domain.tasks import effective_deadline
from app.models.tables import Assignment, CleaningOffer, CleaningTask, Reservation, ReservationSyncRun, ScheduleRun
from app.services.audit import write_audit
from app.services.checkout_policy import baseline_checkout, booked_checkout, effective_checkin
from app.services.task_generation import recompute_property_tasks
from app.services.weekly_availability import NY


def _fresh(session: Session, now: datetime) -> bool:
    run = session.scalar(
        select(ReservationSyncRun)
        .where(ReservationSyncRun.provider == "hospitable")
        .order_by(ReservationSyncRun.started_at.desc(), ReservationSyncRun.id.desc())
        .limit(1)
    )
    return bool(
        run
        and run.status == "succeeded"
        and run.source_as_of is not None
        and timedelta(0)
        <= now - run.source_as_of
        <= timedelta(minutes=get_settings().integration_stale_threshold_minutes)
    )


def _departure_task(session: Session, reservation: Reservation) -> CleaningTask | None:
    return session.scalar(
        select(CleaningTask)
        .where(CleaningTask.source_reservation_id == reservation.id, CleaningTask.kind == "departure")
        .with_for_update()
    )


def _late_checkout(
    session: Session, reservation: Reservation, target: datetime, now: datetime
) -> tuple[datetime | None, str]:
    booked = booked_checkout(session, reservation)
    existing = baseline_checkout(session, reservation)
    if target <= booked or target.astimezone(NY).date() != booked.astimezone(NY).date():
        return None, "outside_checkout_day"
    task = _departure_task(session, reservation)
    if (
        task is None
        or task.status not in {"ready", "assigned"}
        or task.confirmed_checkout_at is not None
        or task.duration_minutes is None
        or target + timedelta(minutes=task.duration_minutes)
        > effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
    ):
        return None, "cleaning_window_unconfirmed"
    if target <= existing:
        return target, "existing_checkout_confirmed"
    if now >= booked:
        return None, "checkout_already_due"
    assignments = list(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.task_id == task.id,
                Assignment.status.in_(("planned", "published", "acknowledged", "blocked", "completed")),
            )
            .with_for_update()
        )
    )
    if any(
        assignment.status in {"blocked", "completed"}
        or assignment.planned_start <= now
        or assignment.planned_start < target
        or assignment.planned_end > effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        or assignment.duration_minutes != task.duration_minutes
        for assignment in assignments
    ):
        return None, "cleaning_assignment_conflicts"
    open_offer = session.scalar(
        select(CleaningOffer.id)
        .where(CleaningOffer.task_id == task.id, CleaningOffer.status.in_(("open", "expired")))
        .limit(1)
    )
    if open_offer is not None:
        return None, "cleaning_offer_requires_review"
    released = session.scalar(
        select(ScheduleRun.id)
        .where(ScheduleRun.service_date == booked.astimezone(NY).date(), ScheduleRun.published_at.is_not(None))
        .limit(1)
    )
    if target <= booked + timedelta(hours=1) and released is None:
        return target, "checkout_day_schedule_unreleased"
    if target > booked + timedelta(hours=1) and any(
        assignment.status in {"published", "acknowledged"} for assignment in assignments
    ):
        return target, "published_cleaning_still_fits"
    return None, "schedule_confirmation_required"


def _early_checkin(
    session: Session, reservation: Reservation, others: list[Reservation], target: datetime, now: datetime
) -> tuple[datetime | None, str]:
    booked = reservation.checkin_at
    if target >= booked or target.astimezone(NY).date() != booked.astimezone(NY).date():
        return None, "outside_checkin_day"
    if now >= booked and effective_checkin(reservation) == booked:
        return None, "checkin_already_due"
    previous = max(
        (other for other in others if effective_checkin(other) < booked),
        key=lambda other: baseline_checkout(session, other),
        default=None,
    )
    if previous is None:
        return None, "cleaning_completion_unconfirmed"
    task = _departure_task(session, previous)
    if task is None or task.status != "completed" or task.completed_at is None:
        return None, "cleaning_completion_unconfirmed"
    departed = task.confirmed_checkout_at if task.confirmed_checkout_reservation_version == previous.version else None
    ready_at = max(task.completed_at, departed or baseline_checkout(session, previous))
    checkin = max(target, ready_at)
    if ready_at > now or checkin >= booked:
        return None, "property_not_ready"
    return checkin, "turnover_completed"


def assess(
    session: Session, reservation: Reservation, request_id: str, kind: str, target: datetime, now: datetime
) -> dict[str, object]:
    start, end = effective_checkin(reservation), baseline_checkout(session, reservation)

    def result(reason: str, approved: datetime | None = None) -> dict[str, object]:
        response: dict[str, object] = {
            "request_id": request_id,
            "decision": "pending" if approved is None else "approved",
            "reason": reason,
            "effective_checkin_at": effective_checkin(reservation).isoformat(),
            "effective_checkout_at": baseline_checkout(session, reservation).isoformat(),
        }
        if approved is not None:
            response["target_at"] = approved.isoformat()
        return response

    if kind not in {"early_checkin", "late_checkout"}:
        raise ValueError("Choose an early check-in or late checkout.")
    if not _fresh(session, now):
        return result("reservation_sync_unconfirmed")
    others = list(
        session.scalars(
            select(Reservation).where(
                Reservation.property_id == reservation.property_id,
                Reservation.id != reservation.id,
                Reservation.status == "active",
            )
        )
    )
    if kind == "early_checkin":
        approved, reason = _early_checkin(session, reservation, others, target, now)
        if approved is None:
            return result(reason)
        new_start, new_end = min(start, approved), end
    else:
        approved, reason = _late_checkout(session, reservation, target, now)
        if approved is None:
            return result(reason)
        new_start, new_end = start, max(end, approved)
    for other in others:
        other_end = baseline_checkout(session, other)
        if kind == "early_checkin":
            turnover = (
                _departure_task(session, other) if effective_checkin(other) < start and other_end > new_start else None
            )
            if turnover and turnover.status == "completed" and turnover.confirmed_checkout_at is not None:
                if turnover.confirmed_checkout_reservation_version == other.version:
                    other_end = turnover.confirmed_checkout_at
        if effective_checkin(other) < new_end and other_end > new_start:
            return result("another_guest_stay_conflicts")
    if (new_start, new_end) != (start, end):
        if kind == "early_checkin":
            reservation.approved_guest_checkin_at = new_start
        else:
            reservation.approved_guest_checkout_at = new_end
        clock = FrozenClock(now)
        recompute_property_tasks(session, clock=clock, property_id=reservation.property_id)
        write_audit(
            session,
            clock=clock,
            actor_type="system",
            actor_subject="guest-recovery",
            action="reservation.guest_stay_approved",
            entity_type="reservation",
            entity_id=reservation.id,
            correlation_id=reservation.id,
            reason=reason,
            redacted_changes={"kind": kind, "target_at": approved.astimezone(UTC).isoformat()},
            request_id=request_id,
        )
    return result(reason, approved)
