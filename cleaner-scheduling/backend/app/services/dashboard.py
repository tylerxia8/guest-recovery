from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.routing.baseline import DrivingBaseline
from app.clock import Clock
from app.config import Settings
from app.domain.principal import Principal
from app.domain.tasks import effective_deadline
from app.models import (
    Assignment,
    Cleaner,
    CleaningTask,
    EmailDelivery,
    OperationalException,
    Property,
    Reservation,
    ReservationSyncRun,
    RoutingSnapshot,
    ScheduleRun,
    SmsMessage,
)
from app.services.redaction import redact_for_viewer
from app.services.reservation_sync import publication_blocked
from app.services.weekly_availability import effective_availability

NY = ZoneInfo("America/New_York")
_UNCOVERED = {
    "NO_CAPACITY",
    "NO_ROUTE",
    "NO_LOCATION",
    "INELIGIBLE",
    "MISSING_DURATION",
    "DEADLINE_IMPOSSIBLE",
    "LOCK_CONFLICT",
    "MISSING_READINESS_BUFFER",
}


def build_daily_operations(
    session: Session, *, clock: Clock, settings: Settings, service_date: date, principal: Principal
) -> dict[str, object]:
    now = clock.now()
    today = now.astimezone(NY).date()
    minimum, maximum = today - timedelta(days=7), today + timedelta(days=90)
    if not minimum <= service_date <= maximum:
        from app.domain.errors import AppError

        raise AppError("validation_error", "Choose a date within the past 7 or next 90 days.", 422)
    start = datetime.combine(service_date, time.min, NY).astimezone(UTC)
    end = datetime.combine(service_date + timedelta(days=1), time.min, NY).astimezone(UTC)
    reference = now if service_date == today else start
    houses = {row.id: row.name for row in session.scalars(select(Property).where(Property.active.is_(True)))}
    reservations = session.scalars(
        select(Reservation)
        .where(
            Reservation.status == "active",
            Reservation.property_id.in_(houses),
            Reservation.checkout_at >= start,
            Reservation.checkin_at < end,
        )
        .order_by(Reservation.checkin_at, Reservation.id)
    ).all()
    occupied: list[dict[str, object]] = []
    arrivals: list[dict[str, object]] = []
    departures: list[dict[str, object]] = []
    for row in reservations:
        item: dict[str, object] = {
            "id": str(row.id),
            "property_id": str(row.property_id),
            "property_name": houses[row.property_id],
            "checkin_at": row.checkin_at.isoformat(),
            "checkout_at": row.checkout_at.isoformat(),
        }
        if row.checkin_at <= reference < row.checkout_at:
            occupied.append(item)
        if start <= row.checkin_at < end:
            arrivals.append(item)
        if start <= row.checkout_at < end:
            departures.append(item)
    turnovers: list[dict[str, object]] = []
    for departure in departures:
        arrival = next(
            (
                row
                for row in arrivals
                if row["property_id"] == departure["property_id"]
                and row["id"] != departure["id"]
                and datetime.fromisoformat(str(row["checkin_at"]))
                >= datetime.fromisoformat(str(departure["checkout_at"]))
            ),
            None,
        )
        if arrival is not None:
            turnovers.append(
                {
                    "id": departure["id"],
                    "property_name": departure["property_name"],
                    "checkout_at": departure["checkout_at"],
                    "checkin_at": arrival["checkin_at"],
                }
            )
    dashboard = build_dashboard(session, clock=clock, settings=settings, service_date=service_date, principal=principal)
    tasks = cast(list[dict[str, object]], dashboard["today"])
    return {
        "service_date": service_date.isoformat(),
        "business_date": today.isoformat(),
        "min_date": minimum.isoformat(),
        "max_date": maximum.isoformat(),
        "occupancy_as_of": reference.isoformat(),
        "houses": [
            {"id": str(identity), "name": name}
            for identity, name in sorted(houses.items(), key=lambda item: item[1].casefold())
        ],
        "occupied": occupied,
        "check_ins": arrivals,
        "check_outs": departures,
        "turnovers": turnovers,
        "tasks": tasks,
        "sync_health": dashboard["sync_health"],
        "mode": "hospitable_read_only" if settings.hospitable_read_only else "mock",
    }


def build_dashboard(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    service_date: date,
    principal: Principal,
) -> dict[str, object]:
    """Build dashboard data for a given service date.

    Response includes assignment_version which maps to Assignment.version (the
    optimistic lock version used for If-Match headers on lock/unlock/override).
    """
    tomorrow = service_date + timedelta(days=1)
    tasks = list(session.scalars(select(CleaningTask)).all())
    assignments = list(session.scalars(select(Assignment)).all())
    properties = {row.id: row.name for row in session.scalars(select(Property)).all()}
    reservations = {row.id: row for row in session.scalars(select(Reservation))}
    cleaners = {row.id: row.display_name for row in session.scalars(select(Cleaner)).all()}
    current: dict[object, Assignment] = {}
    for row in sorted(assignments, key=lambda row: (row.assignment_version, row.created_at), reverse=True):
        if row.status in {"published", "acknowledged", "completed", "blocked"}:
            current.setdefault(row.task_id, row)

    def on_day(task: CleaningTask, day: date) -> bool:
        if task.status == "canceled":
            return False
        assignment = current.get(task.id)
        if assignment is not None:
            return assignment.service_date == day
        if task.status == "completed":
            return task.completed_at is not None and _local_date(task.completed_at) == day
        deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        return _local_date(task.release_at) <= day <= _local_date(deadline)

    today_tasks = [task for task in tasks if on_day(task, service_date)]
    tomorrow_tasks = [task for task in tasks if on_day(task, tomorrow)]
    baseline = session.info.get("driving_baseline")
    travel_sources = dict(
        session.execute(
            select(ScheduleRun.id, RoutingSnapshot.provider).join(
                RoutingSnapshot, ScheduleRun.routing_snapshot_id == RoutingSnapshot.id
            )
        ).all()
    )
    snapshot_points = dict(
        session.execute(
            select(ScheduleRun.id, RoutingSnapshot.matrix_seconds).join(
                RoutingSnapshot, ScheduleRun.routing_snapshot_id == RoutingSnapshot.id
            )
        ).all()
    )
    task_properties = {task.id: properties.get(task.property_id, "Unknown property") for task in tasks}
    travel_from: dict[object, str] = {}
    previous: dict[tuple[object, date], str] = {}
    for assignment in sorted(current.values(), key=lambda row: (row.planned_start, str(row.id))):
        key = (assignment.cleaner_id, assignment.service_date)
        staging = baseline.staging_name if isinstance(baseline, DrivingBaseline) else "Demo starting point"
        origin = staging if travel_sources.get(assignment.schedule_run_id) == "osrm_cached" else "Demo starting point"
        route_points = snapshot_points.get(assignment.schedule_run_id, {}).get("points", [])
        if isinstance(route_points, list) and f"synthetic:start-{assignment.cleaner_id.hex}" in route_points:
            origin = "Cleaner starting address"
        travel_from[assignment.id] = previous.get(key, origin)
        previous[key] = task_properties.get(assignment.task_id, "Previous house")

    def summary(task: CleaningTask) -> dict[str, object]:
        body = _task_summary(task)
        reservation = reservations.get(task.source_reservation_id)
        body["can_confirm_early_checkout"] = (
            bool({"owner", "manager"}.intersection(principal.roles))
            and task.status not in {"completed", "canceled"}
            and task.confirmed_checkout_at is None
            and reservation is not None
            and reservation.status == "active"
            and reservation.checkin_at <= now < reservation.checkout_at
            and reservation.checkout_at.astimezone(NY).date() == now.astimezone(NY).date()
        )
        body["property_name"] = properties.get(task.property_id, "Unknown property")
        assignment = current.get(task.id)
        body["cleaner_name"] = None if assignment is None else cleaners.get(assignment.cleaner_id)
        body["planned_start"] = None if assignment is None else assignment.planned_start.isoformat()
        body["planned_end"] = None if assignment is None else assignment.planned_end.isoformat()
        body["acknowledged"] = assignment is not None and assignment.acknowledged_at is not None
        body["assignment_id"] = None if assignment is None else str(assignment.id)
        body["assignment_version"] = None if assignment is None else assignment.version
        body["assignment_status"] = None if assignment is None else assignment.status
        body["is_locked"] = assignment is not None and assignment.is_locked
        body["lock_reason"] = None if assignment is None else assignment.lock_reason
        body["locked_by"] = None if assignment is None else assignment.locked_by
        body["cleaner_id"] = None if assignment is None else str(assignment.cleaner_id)
        body["deadline_conflict"] = (
            assignment is not None
            and assignment.planned_end > effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
            and task.status != "completed"
        )
        body["scheduled_date"] = None if assignment is None else assignment.service_date.isoformat()
        body["completed_at"] = None if task.completed_at is None else task.completed_at.isoformat()
        body["cleaned"] = task.status == "completed"
        body["overdue"] = _is_late(task, clock.now())
        body["needs_assignment"] = assignment is None and task.status not in {"completed", "canceled"}
        body["travel_before_seconds"] = None if assignment is None else assignment.travel_before_seconds
        body["travel_from"] = None if assignment is None else travel_from.get(assignment.id)
        body["travel_source"] = None if assignment is None else travel_sources.get(assignment.schedule_run_id, "mock")
        return body

    today_assignments = [row for row in current.values() if row.service_date == service_date]
    availability = effective_availability(session, service_date) + effective_availability(session, tomorrow)
    exceptions = list(
        session.scalars(
            select(OperationalException)
            .where(OperationalException.status.in_(("open", "acknowledged")))
            .order_by(OperationalException.opened_at)
        ).all()
    )
    messages = list(session.scalars(select(SmsMessage)).all())
    sync = session.scalar(select(ReservationSyncRun).order_by(ReservationSyncRun.started_at.desc()).limit(1))
    now = clock.now()
    email_counts = _count(
        list(
            session.scalars(
                select(EmailDelivery.status).where(
                    EmailDelivery.kind == "assignment",
                    EmailDelivery.service_date.in_((service_date, tomorrow)),
                )
            )
        )
    )
    email_codes = {
        "AUTOMATIC_SCHEDULE_REVIEW",
        "AUTOMATIC_SCHEDULE_BLOCKED",
        "CLEANER_EMAIL_MISSING",
        "ASSIGNMENT_EMAIL_BLOCKED",
        "EMAIL_DELIVERY_UNCERTAIN",
        "EMAIL_DELIVERY_FAILED",
        "CLEANING_UNCLAIMED",
    }
    email_problems = [
        {
            "id": str(row.id),
            "code": row.code,
            "cleaner_name": row.details.get("cleaner_name"),
            "property_name": row.details.get("property_name"),
            "service_date": row.details.get("service_date"),
        }
        for row in exceptions
        if row.code in email_codes
    ]
    body: dict[str, object] = {
        "email_automation": {
            "enabled": settings.local_email_automation_enabled,
            "dispatch_mode": "evening",
            "local_time": settings.dispatch_local_time,
            "timezone": "America/New_York",
            "delivery_counts": email_counts,
            "problems": email_problems,
        },
        "routing": baseline.summary() if isinstance(baseline, DrivingBaseline) else {"mode": "synthetic"},
        "daily_travel_seconds": sum(row.travel_before_seconds for row in today_assignments),
        "business_date": now.astimezone(NY).date().isoformat(),
        "mode": "hospitable_read_only" if settings.hospitable_read_only else "mock",
        "service_date": service_date.isoformat(),
        "tomorrow_date": tomorrow.isoformat(),
        "today": [summary(task) for task in sorted(today_tasks, key=lambda row: (row.release_at, str(row.id)))],
        "tomorrow": [summary(task) for task in sorted(tomorrow_tasks, key=lambda row: (row.release_at, str(row.id)))],
        "overdue": [
            summary(task)
            for task in sorted(tasks, key=lambda row: (row.release_at, str(row.id)))
            if _is_late(task, now)
        ],
        "unassigned_count": sum(
            1 for task in today_tasks if current.get(task.id) is None and task.status not in {"completed", "canceled"}
        ),
        "evening_dispatch": {
            "local_time": settings.dispatch_local_time,
            "timezone": "America/New_York",
            "mode": "local_email" if settings.local_email_automation_enabled else "mock_only",
        },
        "availability": _count([row.status for row in availability]),
        "assignment_counts": _count([row.status for row in today_assignments]),
        "ack_counts": {
            "acknowledged": sum(1 for row in today_assignments if row.acknowledged_at is not None),
            "awaiting": sum(
                1 for row in today_assignments if row.status in {"published", "planned"} and row.acknowledged_at is None
            ),
        },
        "completion_counts": {
            "completed": sum(1 for task in today_tasks if task.status == "completed"),
            "open": sum(1 for task in today_tasks if task.status not in {"completed", "canceled"}),
        },
        "exceptions": [_exception_summary(row) for row in exceptions],
        "sync_health": {
            "status": None if sync is None else sync.status,
            "publication_blocked": publication_blocked(session, clock=clock, settings=settings),
            "source_as_of": None if sync is None or sync.source_as_of is None else sync.source_as_of.isoformat(),
            "stale": publication_blocked(session, clock=clock, settings=settings),
        },
        "sms_delivery_counts": _count([row.status for row in messages]),
        "pending_deadline_count": sum(1 for task in tasks if task.status == "pending_deadline"),
        "uncovered_count": sum(1 for row in exceptions if row.code in _UNCOVERED),
        "late_count": sum(1 for task in tasks if _is_late(task, now)),
        "dispatch_enabled": False,
        "integration_mode": "mock",
    }
    if _viewer_only(principal):
        redacted = redact_for_viewer(body)
        if isinstance(redacted, dict):
            return {str(key): value for key, value in redacted.items()}
    return body


def _viewer_only(principal: Principal) -> bool:
    return "viewer" in principal.roles and "manager" not in principal.roles and "owner" not in principal.roles


def _local_date(instant: datetime) -> date:
    return instant.astimezone(NY).date()


def _is_late(task: CleaningTask, now: datetime) -> bool:
    if task.status in {"completed", "canceled"}:
        return False
    return effective_deadline(task.release_at, task.deadline_at, task.manager_target_at) < now


def _task_summary(task: CleaningTask) -> dict[str, object]:
    return {
        "id": str(task.id),
        "version": task.version,
        "confirmed_checkout_at": None if task.confirmed_checkout_at is None else task.confirmed_checkout_at.isoformat(),
        "property_id": str(task.property_id),
        "status": task.status,
        "release_at": task.release_at.isoformat(),
        "deadline_at": effective_deadline(task.release_at, task.deadline_at, task.manager_target_at).isoformat(),
        "pending": task.status == "pending_deadline",
        "duration_minutes": task.duration_minutes,
    }


def _exception_summary(row: OperationalException) -> dict[str, object]:
    return {
        "id": str(row.id),
        "version": row.version,
        "code": row.code,
        "severity": row.severity,
        "status": row.status,
        "owner_subject": row.owner_subject,
        "opened_at": row.opened_at.isoformat(),
        "task_id": None if row.task_id is None else str(row.task_id),
    }


def _count(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))
