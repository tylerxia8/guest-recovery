from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.factory import build_routing_service
from app.adapters.routing.baseline import DrivingBaseline
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.scheduling import Job, Placed, Shift, SolveResult, Travel, job_limit, solve, validate
from app.domain.tasks import effective_deadline
from app.models import (
    Assignment,
    Cleaner,
    CleanerAvailability,
    CleanerScheduleVersion,
    CleanerWeeklyHours,
    CleaningOffer,
    CleaningTask,
    OperationalException,
    OutboxEvent,
    PolicyVersion,
    Property,
    PropertyInstruction,
    Reservation,
    RoutingSnapshot,
    ScheduleDay,
    ScheduleRun,
)
from app.services.audit import write_audit
from app.services.eligibility import eligible_properties
from app.services.field_cipher import protect_text
from app.services.instructions import latest_instruction
from app.services.local_routing import current_local_baseline
from app.services.property_groups import property_group
from app.services.reservation_sync import publication_blocked
from app.services.schedule_freshness import NY, bump_task
from app.services.task_generation import departure_requires_review
from app.services.weekly_availability import cleaner_ready_for_scheduling, effective_availability
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID

_LIVE = ("planned", "published", "acknowledged", "blocked", "completed")


def assert_manual_publication(settings: Settings) -> None:
    mock_providers = (settings.routing_provider, settings.sms_provider, settings.reservation_provider)
    if settings.hospitable_read_only:
        if (
            not settings.local_scheduling_enabled
            or not settings.local_free_routing
            or mock_providers != ("mock", "mock", "hospitable")
        ):
            raise AppError("local_scheduling_disabled", "Local manual scheduling is not enabled.", 403)
    elif mock_providers != ("mock", "mock", "mock"):
        raise AppError("production_adapter_unavailable", "Scheduling publication stays on mock providers.", 403)
    if settings.production_dispatch_enabled or settings.production_integrations_enabled:
        raise AppError("dispatch_disabled", "Operational schedule publication is disabled.", 403)
    if settings.google_maps_api_key or settings.twilio_auth_token or settings.twilio_account_sid:
        raise AppError("credentials_not_permitted", "Provider credentials are not permitted.", 403)


@dataclass(frozen=True)
class _PreservedSnapshot:
    """Snapshot of preserved assignment data for validation."""

    task_id: str
    cleaner_id: str
    planned_start: datetime
    planned_end: datetime
    completed_at: datetime | None
    status: str
    travel_before_seconds: int


@dataclass(frozen=True)
class PreviewInputs:
    """Solver inputs gathered in read phase, used without a DB session."""

    fingerprint: str
    day_revision: int
    revisions: dict[str, object]
    policy_version: int
    jobs: list[Job]
    shifts: list[Shift]
    travel: Travel
    routing_snapshot_id: uuid.UUID
    extra_uncovered: list[dict[str, str]]
    solver_time_limit: int
    service_date: date
    actor: str
    preserved_snapshots: tuple[_PreservedSnapshot, ...]


def gather_preview_inputs(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    service_date: date,
    actor: str,
    preserved_assignment_ids: tuple[uuid.UUID, ...] = (),
) -> PreviewInputs:
    """Gather all inputs for schedule preview (read phase under lock).

    Does NOT run the solver. Returns a frozen dataclass containing all data
    needed to run the solver without a database session.
    """
    assert_manual_publication(settings)
    if settings.hospitable_read_only:
        if current_local_baseline(session, settings) is None:
            raise AppError("local_routes_unavailable", "Refresh saved driving routes before scheduling.", 409)
        if publication_blocked(session, clock=clock, settings=settings):
            raise AppError("stale_source", "Reservation sync is stale. Preview is blocked.", 409)
    policy = _approved_policy(session, clock)
    if policy is None:
        raise AppError("policy_not_approved", "An approved policy is required before preview.", 403)
    fingerprint, day, revisions = _fingerprint(session, service_date, policy.version_number)
    preserved = _preserved_assignments(session, clock, service_date, preserved_assignment_ids)
    if preserved:
        revisions["preserved_assignments"] = [_assignment_revision(row) for row in preserved]
    jobs, shifts, travel, snapshot, extra_uncovered = _inputs(session, clock, service_date, preserved)
    raw_limit = policy.settings.get("solver_time_limit_seconds", settings.solver_time_limit_seconds)
    limit = raw_limit if isinstance(raw_limit, int) else settings.solver_time_limit_seconds
    preserved_snapshots = tuple(
        _PreservedSnapshot(
            task_id=str(row.task_id),
            cleaner_id=str(row.cleaner_id),
            planned_start=row.planned_start,
            planned_end=row.planned_end,
            completed_at=row.completed_at,
            status=row.status,
            travel_before_seconds=row.travel_before_seconds,
        )
        for row in preserved
    )
    return PreviewInputs(
        fingerprint=fingerprint,
        day_revision=day.revision,
        revisions=revisions,
        policy_version=policy.version_number,
        jobs=jobs,
        shifts=shifts,
        travel=travel,
        routing_snapshot_id=snapshot.id,
        extra_uncovered=extra_uncovered,
        solver_time_limit=limit,
        service_date=service_date,
        actor=actor,
        preserved_snapshots=preserved_snapshots,
    )


def run_preview_solver(inputs: PreviewInputs, clock: Clock) -> tuple[SolveResult, datetime, datetime]:
    """Run solver with gathered inputs (no DB access needed).

    Returns (result, started_at, finished_at).
    """
    started = clock.now()
    result = solve(inputs.jobs, inputs.shifts, inputs.travel, time_limit_seconds=inputs.solver_time_limit)
    finished = clock.now()
    return result, started, finished


def store_preview_run(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    inputs: PreviewInputs,
    result: SolveResult,
    started: datetime,
    finished: datetime,
) -> ScheduleRun:
    """Store solver result as ScheduleRun (write phase under lock).

    Re-checks fingerprint and day_revision before storing to detect concurrent changes.
    Returns existing run if fingerprint matches a recent preview.
    """
    assert_manual_publication(settings)
    policy = _approved_policy(session, clock)
    if policy is None:
        raise AppError("policy_not_approved", "An approved policy is required before preview.", 403)
    fresh_fingerprint, day, _revisions = _fingerprint(session, inputs.service_date, policy.version_number)
    if fresh_fingerprint != inputs.fingerprint or day.revision != inputs.day_revision:
        raise AppError("stale_preview", "Schedule inputs changed during solver execution.", 409)
    existing = session.scalar(
        select(ScheduleRun).where(
            ScheduleRun.service_date == inputs.service_date,
            ScheduleRun.input_fingerprint == inputs.fingerprint,
            ScheduleRun.day_revision == inputs.day_revision,
            ScheduleRun.status == "preview",
        )
    )
    if existing is not None:
        return existing
    _validate_preserved_plan_from_snapshots(inputs.preserved_snapshots, result.placed, clock.now())
    errors = validate(result, inputs.jobs, inputs.shifts, inputs.travel)
    uncovered = [{"task_id": task_id, "reason": reason} for task_id, reason in result.uncovered]
    uncovered.extend(inputs.extra_uncovered)
    now = clock.now()
    run = ScheduleRun(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        service_date=inputs.service_date,
        status="failed" if errors else "preview",
        input_fingerprint=inputs.fingerprint,
        input_revisions=inputs.revisions,
        solver_version=result.solver_version,
        solver_status="failed" if errors else result.solver_status,
        objective_values={
            "coverage": result.coverage,
            "travel_seconds": result.travel_seconds,
            "elapsed_seconds": result.elapsed_seconds,
            "validation_errors": errors,
            **result.objective,
        },
        candidate_plan={
            "placed": [
                {
                    "task_id": item.task_id,
                    "cleaner_id": item.cleaner_id,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "travel_before_seconds": int(item.travel_before.total_seconds()),
                }
                for item in result.placed
            ]
        },
        uncovered=uncovered,
        started_at=started,
        finished_at=finished,
        published_at=None,
        routing_snapshot_id=inputs.routing_snapshot_id,
        policy_version=inputs.policy_version,
        day_revision=inputs.day_revision,
        created_by=inputs.actor,
    )
    session.add(run)
    session.flush()
    return run


def preview_schedule(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    service_date: date,
    actor: str,
    preserved_assignment_ids: tuple[uuid.UUID, ...] = (),
) -> ScheduleRun:
    assert_manual_publication(settings)
    if settings.hospitable_read_only:
        if current_local_baseline(session, settings) is None:
            raise AppError("local_routes_unavailable", "Refresh saved driving routes before scheduling.", 409)
        if publication_blocked(session, clock=clock, settings=settings):
            raise AppError("stale_source", "Reservation sync is stale. Preview is blocked.", 409)
    policy = _approved_policy(session, clock)
    if policy is None:
        raise AppError("policy_not_approved", "An approved policy is required before preview.", 403)
    started = clock.now()
    fingerprint, day, revisions = _fingerprint(session, service_date, policy.version_number)
    preserved = _preserved_assignments(session, clock, service_date, preserved_assignment_ids)
    if preserved:
        revisions["preserved_assignments"] = [_assignment_revision(row) for row in preserved]
    jobs, shifts, travel, snapshot, extra_uncovered = _inputs(session, clock, service_date, preserved)
    raw_limit = policy.settings.get("solver_time_limit_seconds", settings.solver_time_limit_seconds)
    limit = raw_limit if isinstance(raw_limit, int) else settings.solver_time_limit_seconds
    result = solve(jobs, shifts, travel, time_limit_seconds=limit)
    errors = validate(result, jobs, shifts, travel)
    _validate_preserved_plan(preserved, result.placed, clock.now())
    uncovered = [{"task_id": task_id, "reason": reason} for task_id, reason in result.uncovered]
    uncovered.extend(extra_uncovered)
    now = clock.now()
    run = ScheduleRun(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        service_date=service_date,
        status="failed" if errors else "preview",
        input_fingerprint=fingerprint,
        input_revisions=revisions,
        solver_version=result.solver_version,
        solver_status="failed" if errors else result.solver_status,
        objective_values={
            "coverage": result.coverage,
            "travel_seconds": result.travel_seconds,
            "elapsed_seconds": result.elapsed_seconds,
            "validation_errors": errors,
            **result.objective,
        },
        candidate_plan={
            "placed": [
                {
                    "task_id": item.task_id,
                    "cleaner_id": item.cleaner_id,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "travel_before_seconds": int(item.travel_before.total_seconds()),
                }
                for item in result.placed
            ]
        },
        uncovered=uncovered,
        started_at=started,
        finished_at=now,
        published_at=None,
        routing_snapshot_id=snapshot.id,
        policy_version=policy.version_number,
        day_revision=day.revision,
        created_by=actor,
    )
    session.add(run)
    session.flush()
    return run


def publish_schedule(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    run_id: uuid.UUID,
    expected_fingerprint: str,
    expected_day_revision: int,
    expected_version: int,
    actor: str,
) -> dict[str, object]:
    assert_manual_publication(settings)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    if settings.hospitable_read_only and current_local_baseline(session, settings) is None:
        raise AppError("local_routes_unavailable", "Refresh saved driving routes before scheduling.", 409)
    policy = _approved_policy(session, clock)
    if policy is None:
        raise AppError("policy_not_approved", "An approved policy is required before publication.", 403)
    if publication_blocked(session, clock=clock, settings=settings):
        raise AppError("stale_source", "Reservation sync is stale. Publication is blocked.", 409)
    run = session.scalar(select(ScheduleRun).where(ScheduleRun.id == run_id).with_for_update())
    if run is None:
        raise AppError("not_found", "Schedule preview was not found.", 404)
    if run.version != expected_version:
        raise AppError("stale_version", "Schedule run version is stale.", 409)
    if run.status == "failed":
        raise AppError("infeasible", "Independent validation rejected the plan.", 422)
    if run.input_revisions.get("routing_baseline", "mock-v1") != _routing_identity(session):
        raise AppError("stale_preview", "Driving baseline changed. Generate a fresh preview.", 409)
    day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == run.service_date).with_for_update())
    if (
        day is None
        or day.revision != expected_day_revision
        or run.day_revision != expected_day_revision
        or run.input_fingerprint != expected_fingerprint
    ):
        raise AppError("stale_preview", "Schedule preview is stale.", 409)
    fresh_fingerprint, _fresh_day, _fresh_revisions = _fingerprint(session, run.service_date, policy.version_number)
    if run.policy_version != policy.version_number or fresh_fingerprint != run.input_fingerprint:
        raise AppError("stale_preview", "Schedule inputs changed. Generate a fresh preview.", 409)
    preserved_revisions = run.input_revisions.get("preserved_assignments", [])
    try:
        if not isinstance(preserved_revisions, list):
            raise ValueError("Invalid preserved assignments")
        preserved_ids = tuple(uuid.UUID(row["id"]) for row in preserved_revisions)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AppError("invalid_preview", "The saved preserved assignments are invalid.", 409) from None
    preserved = _preserved_assignments(session, clock, run.service_date, preserved_ids)
    if [_assignment_revision(row) for row in preserved] != preserved_revisions:
        raise AppError("stale_preview", "Preserved assignments changed. Generate a fresh preview.", 409)
    jobs, shifts, travel, _snapshot, _extra = _inputs(session, clock, run.service_date, preserved)
    jobs_by_id = {job.task_id: job for job in jobs}
    try:
        rows = run.candidate_plan["placed"]
        if not isinstance(rows, list):
            raise ValueError("Invalid stored plan")
        placed: list[Placed] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid stored placement")
            task_id, cleaner_id = row["task_id"], row["cleaner_id"]
            start, end = row["start"], row["end"]
            seconds = row["travel_before_seconds"]
            if (
                not isinstance(task_id, str)
                or not isinstance(cleaner_id, str)
                or not isinstance(start, str)
                or not isinstance(end, str)
                or not isinstance(seconds, int)
                or isinstance(seconds, bool)
                or seconds < 0
            ):
                raise ValueError("Invalid stored placement")
            start_at, end_at = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if start_at.tzinfo is None or end_at.tzinfo is None:
                raise ValueError("Stored placement requires timezone")
            placed.append(
                Placed(task_id, cleaner_id, start_at, end_at, timedelta(seconds=seconds), jobs_by_id[task_id].place)
            )
        uncovered = tuple((row["task_id"], row["reason"]) for row in run.uncovered if row["task_id"] in jobs_by_id)
        stored_extra = {(row["task_id"], row["reason"]) for row in run.uncovered if row["task_id"] not in jobs_by_id}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AppError("invalid_preview", "The saved plan is invalid. Generate a fresh preview.", 409) from None
    placed_ids = {item.task_id for item in placed}
    uncovered_ids = {task_id for task_id, _reason in uncovered}
    if (
        placed_ids & uncovered_ids
        or placed_ids | uncovered_ids != set(jobs_by_id)
        or len(uncovered_ids) != len(uncovered)
        or stored_extra != {(row["task_id"], row["reason"]) for row in _extra}
    ):
        raise AppError(
            "stale_preview", "The saved plan no longer covers the current work. Generate a fresh preview.", 409
        )
    result = SolveResult(
        placed=tuple(placed),
        uncovered=uncovered,
        travel_seconds=sum(int(item.travel_before.total_seconds()) for item in placed),
        solver_status=run.solver_status,
        coverage=len(placed),
        solver_version=run.solver_version,
        elapsed_seconds=0.0,
        objective={},
    )
    if validate(result, jobs, shifts, travel):
        raise AppError("infeasible", "Independent validation rejected the plan.", 422)
    _reject_broken_locks(jobs, result.placed)
    now = clock.now()
    _validate_preserved_plan(preserved, result.placed, clock.now())
    placed_by_task = {item.task_id: item for item in result.placed}
    for row in preserved:
        if row.status == "completed" or row.planned_start <= now:
            continue
        seconds = int(placed_by_task[str(row.task_id)].travel_before.total_seconds())
        if row.travel_before_seconds != seconds:
            write_audit(
                session,
                clock=clock,
                actor_type="operator",
                actor_subject=actor,
                action="assignment.route_updated",
                entity_type="assignment",
                entity_id=row.id,
                correlation_id=run.id,
                reason="validated schedule insertion",
                redacted_changes={"previous_seconds": row.travel_before_seconds, "seconds": seconds},
                request_id=None,
            )
            row.travel_before_seconds = seconds
            row.version += 1
            row.updated_at = now
    _supersede_replaceable(session, run.service_date, now, preserved_ids)
    session.flush()
    versions: dict[str, CleanerScheduleVersion] = {}
    intent_ids: list[str] = []
    for item in result.placed:
        existing = _live_assignment(session, uuid.UUID(item.task_id))
        if existing is not None and (
            existing.id in preserved_ids or existing.is_locked or existing.planned_start <= now
        ):
            continue
        version = versions.get(item.cleaner_id)
        if version is None:
            version = _version(session, now, item.cleaner_id, run)
            versions[item.cleaner_id] = version
            notice = _outbox(
                session,
                now,
                "schedule.cleaner_version",
                version.id,
                version.version_number,
                {
                    "cleaner_id": item.cleaner_id,
                    "service_date": run.service_date.isoformat(),
                    "sms_sent": False,
                },
            )
            intent_ids.append(str(notice.id))
        task = session.get(CleaningTask, uuid.UUID(item.task_id))
        if task is None:
            continue
        instruction = _ensure_instruction(session, clock, settings, task.property_id, actor)
        previous = _latest_assignment(session, task.id)
        session.add(
            Assignment(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                task_id=task.id,
                cleaner_id=uuid.UUID(item.cleaner_id),
                schedule_run_id=run.id,
                cleaner_schedule_version_id=version.id,
                service_date=run.service_date,
                assignment_version=1 if previous is None else previous.assignment_version + 1,
                planned_start=item.start,
                planned_end=item.end,
                duration_minutes=int((item.end - item.start).total_seconds() // 60),
                travel_before_seconds=int(item.travel_before.total_seconds()),
                status="published",
                is_locked=False,
                lock_reason=None,
                locked_by=None,
                acknowledged_at=None,
                completed_at=None,
                supersedes_id=None if previous is None else previous.id,
                instruction_id=instruction.id,
                rate_id=None,
                compensation_snapshot={},
                amount_cents=None,
                currency="USD",
            )
        )
        if task.status not in {"completed", "canceled"}:
            task.status = "assigned"
            task.version += 1
            task.updated_at = now
    for problem in session.scalars(
        select(OperationalException)
        .where(
            OperationalException.task_id.in_([uuid.UUID(item.task_id) for item in result.placed]),
            OperationalException.status.in_(("open", "acknowledged")),
        )
        .with_for_update()
    ):
        if problem.dedupe_key.startswith(f"schedule:{problem.task_id}:"):
            problem.status = "resolved"
            problem.resolved_at = problem.updated_at = now
            problem.resolution_reason = "Covered by the independently validated published schedule."
            problem.version += 1
    for task_id, reason in result.uncovered:
        _open_exception(session, clock, task_id, reason)
    for missing in _extra:
        _open_exception(session, clock, missing["task_id"], missing["reason"])
    run.status = "published"
    run.version += 1
    run.updated_at = now
    run.published_at = now
    if day is None:
        raise AppError("not_found", "Schedule day was not found.", 404)
    day.active_run_id = run.id
    day.revision += 1
    day.updated_at = now
    published = _outbox(
        session,
        now,
        "schedule.published",
        run.id,
        run.version,
        {"service_date": run.service_date.isoformat(), "sms_sent": False},
    )
    intent_ids.append(str(published.id))
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="schedule.published",
        entity_type="schedule_run",
        entity_id=run.id,
        correlation_id=run.id,
        reason="local manual publication" if settings.hospitable_read_only else "synthetic publication",
        redacted_changes={"service_date": run.service_date.isoformat(), "sms_sent": False},
        request_id=None,
    )
    session.flush()
    return {
        "schedule_run_id": str(run.id),
        "day_revision": day.revision,
        "dispatch_intent_ids": intent_ids,
        "uncovered": run.uncovered,
        "sms_sent": False,
    }


def lock_assignment(
    session: Session,
    *,
    clock: Clock,
    assignment_id: uuid.UUID,
    expected_version: int,
    locked: bool,
    reason: str,
    actor: str,
) -> Assignment:
    row = session.execute(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise AppError("not_found", "Assignment was not found.", 404)
    if row.version != expected_version:
        raise AppError("stale_version", "Assignment version is stale.", 409)
    if row.status in {"canceled", "completed", "superseded"}:
        raise AppError("terminal_assignment", "Terminal assignments cannot be locked.", 409)
    if not reason.strip():
        raise AppError("validation_error", "A lock reason is required.", 422)
    row.is_locked = locked
    row.lock_reason = reason
    row.locked_by = actor if locked else None
    row.version += 1
    row.updated_at = clock.now()
    task = session.get(CleaningTask, row.task_id)
    if task is not None:
        bump_task(session, task)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="assignment.locked" if locked else "assignment.unlocked",
        entity_type="assignment",
        entity_id=row.id,
        correlation_id=row.id,
        reason=reason,
        redacted_changes={"is_locked": locked, "version": row.version},
        request_id=None,
    )
    session.flush()
    return row


def _check_override_feasibility(
    session: Session,
    clock: Clock,
    cleaner_id: uuid.UUID,
    task: CleaningTask,
    prop: Property,
    service_date: date,
    planned_start: datetime,
    planned_end: datetime,
    exclude_assignment_id: uuid.UUID | None,
    travel_lookup: Travel,
) -> tuple[list[Assignment], int]:
    """Unified feasibility check for override_assignment.

    Validates breaks, shift bounds, eligibility, deadline window (48h cap),
    overlap over _LIVE statuses, max_jobs, service and duty caps including
    origin travel, and travel through the matrix lookup.

    Returns (other_assignments, travel_before_seconds) if feasible.
    Raises AppError with appropriate code if infeasible.
    """
    if departure_requires_review(session, task.id):
        raise AppError("guest_departure_review", "Guest departure needs manager review before assigning work.", 409)
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("cleaner_inactive", "Cleaner does not exist or is inactive.", 422)

    eligible = eligible_properties(session, cleaner)
    if task.property_id not in eligible:
        raise AppError("ineligible_cleaner", "Cleaner is not eligible for this property.", 422)

    availability = next(
        (a for a in effective_availability(session, service_date) if a.cleaner_id == cleaner_id),
        None,
    )
    if availability is None or availability.status != "available":
        raise AppError("outside_availability", "Cleaner is not available on this date.", 422)

    deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
    if deadline is None or planned_start < task.release_at or planned_end > deadline:
        raise AppError("infeasible", "Override is outside the task window.", 422)

    shift_start, shift_end = _shift_bounds(availability.work_intervals)
    if planned_start < shift_start or planned_end > shift_end:
        raise AppError("outside_shift", "Override is outside the cleaner's shift.", 422)

    breaks = _breaks(availability.unavailable_intervals or [])
    for break_start, break_end in breaks:
        if planned_start < break_end and planned_end > break_start:
            raise AppError("conflicts_with_break", "Override conflicts with an unavailable interval.", 422)

    other_assignments = list(
        session.scalars(
            select(Assignment).where(
                Assignment.cleaner_id == cleaner_id,
                Assignment.service_date == service_date,
                Assignment.status.in_(_LIVE),
            )
        )
    )
    if exclude_assignment_id is not None:
        other_assignments = [a for a in other_assignments if a.id != exclude_assignment_id]

    for other in other_assignments:
        if other.planned_start is not None and other.planned_end is not None:
            if planned_start < other.planned_end and planned_end > other.planned_start:
                _open_exception(session, clock, str(task.id), "LOCK_CONFLICT" if other.is_locked else "NO_CAPACITY")
                raise AppError("infeasible", "Override overlaps another assignment.", 422)

    explicit_limit = availability.max_jobs_override or cleaner.max_jobs
    max_jobs = explicit_limit or max(len(other_assignments) + 1, 1)
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID:
        explicit_limit = min(
            (limit for limit in (availability.max_jobs_override, cleaner.max_jobs) if limit is not None), default=None
        )
        groups = [property_group(prop.name)]
        for other in other_assignments:
            other_task = session.get(CleaningTask, other.task_id)
            other_property = None if other_task is None else session.get(Property, other_task.property_id)
            if other_property is None:
                raise AppError("assignment_unavailable", "An existing assignment needs manager review.", 409)
            groups.append(property_group(other_property.name))
        max_jobs = job_limit(min(explicit_limit or 3, 3), min(explicit_limit or 5, 5), groups)
    if len(other_assignments) + 1 > max_jobs:
        raise AppError("exceeds_max_jobs", f"Override exceeds cleaner's max jobs ({max_jobs}).", 422)

    duration = task.duration_minutes or 0
    total_service = sum(a.duration_minutes or 0 for a in other_assignments) + duration
    service_limit = availability.max_service_override or cleaner.max_service_minutes
    if service_limit is not None and total_service > service_limit:
        raise AppError("exceeds_service_cap", f"Override exceeds cleaner's service cap ({service_limit} min).", 422)

    origin_label = availability.origin_label
    if not origin_label:
        raise AppError("missing_origin", "Cleaner has no starting location for this date.", 422)

    preceding = [a for a in other_assignments if a.planned_end is not None and a.planned_end <= planned_start]
    if preceding:
        predecessor = max(preceding, key=lambda a: a.planned_end)
        pred_task = session.get(CleaningTask, predecessor.task_id)
        pred_prop = session.get(Property, pred_task.property_id) if pred_task else None
        if pred_prop is None:
            raise AppError("missing_predecessor", "Predecessor assignment has no valid property.", 422)
        travel_seconds = travel_lookup(pred_prop.external_id, prop.external_id)
        if travel_seconds is None:
            raise AppError("unreachable", "Travel from predecessor is unreachable.", 422)
        arrival_at = predecessor.planned_end + timedelta(seconds=travel_seconds)
        if arrival_at > planned_start:
            raise AppError("insufficient_travel", "Not enough travel time from previous assignment.", 422)
        travel_before = travel_seconds
    else:
        travel_seconds = travel_lookup(origin_label, prop.external_id)
        if travel_seconds is None:
            raise AppError("unreachable", "Travel from origin is unreachable.", 422)
        arrival_at = shift_start + timedelta(seconds=travel_seconds)
        if arrival_at > planned_start:
            raise AppError("insufficient_travel", "Not enough travel time from origin.", 422)
        travel_before = travel_seconds

    all_starts = [a.planned_start for a in other_assignments if a.planned_start is not None] + [planned_start]
    all_ends = [a.planned_end for a in other_assignments if a.planned_end is not None] + [planned_end]
    if all_starts and all_ends:
        earliest = min(all_starts)
        latest = max(all_ends)
        candidates = [a for a in other_assignments if a.planned_start is not None]
        if not candidates or planned_start < min(a.planned_start for a in candidates):
            first_prop_id = prop.external_id
        else:
            first_assignment = min(candidates, key=lambda a: a.planned_start)
            first_task = session.get(CleaningTask, first_assignment.task_id)
            first_prop = session.get(Property, first_task.property_id) if first_task else None
            if first_prop is None:
                raise AppError("missing_predecessor", "First assignment has no valid property.", 422)
            first_prop_id = first_prop.external_id
        origin_travel = travel_lookup(origin_label, first_prop_id)
        if origin_travel is None:
            raise AppError("unreachable", "Travel from origin to first house is unreachable.", 422)
        total_duty = (latest - earliest).total_seconds() / 60 + origin_travel / 60
        duty_limit = availability.max_duty_override or cleaner.max_duty_minutes
        if duty_limit is not None and total_duty > duty_limit:
            raise AppError("exceeds_duty_cap", f"Override exceeds cleaner's duty cap ({duty_limit} min).", 422)

    return other_assignments, travel_before


def _collect_routing_points(
    session: Session,
    service_date: date,
    cleaner_id: uuid.UUID,
    destination: str,
) -> tuple[str, ...]:
    """Collect all routing points needed for override feasibility check."""
    points: set[str] = {destination}
    availability = next(
        (a for a in effective_availability(session, service_date) if a.cleaner_id == cleaner_id),
        None,
    )
    if availability and availability.origin_label:
        points.add(availability.origin_label)
    other_assignments = list(
        session.scalars(
            select(Assignment).where(
                Assignment.cleaner_id == cleaner_id,
                Assignment.service_date == service_date,
                Assignment.status.in_(_LIVE),
            )
        )
    )
    for assignment in other_assignments:
        task = session.get(CleaningTask, assignment.task_id)
        if task:
            prop = session.get(Property, task.property_id)
            if prop:
                points.add(prop.external_id)
    return tuple(sorted(points))


def override_assignment(
    session: Session,
    *,
    clock: Clock,
    assignment_id: uuid.UUID,
    expected_version: int,
    cleaner_id: uuid.UUID,
    planned_start: datetime,
    reason: str,
    actor: str,
) -> Assignment:
    row = session.execute(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise AppError("not_found", "Assignment was not found.", 404)
    if row.version != expected_version:
        raise AppError("stale_version", "Assignment version is stale.", 409)
    if row.status in {"canceled", "completed", "superseded"}:
        raise AppError("terminal_assignment", "Terminal assignments cannot be overridden.", 409)
    if row.is_locked:
        raise AppError("locked", "Unlock the assignment before overriding it.", 409)
    if planned_start.tzinfo is None:
        raise AppError("validation_error", "planned_start must include an offset.", 422)

    task = session.get(CleaningTask, row.task_id)
    if task is None or task.duration_minutes is None:
        raise AppError("missing_duration", "Assignment duration is missing.", 422)

    prop = session.get(Property, task.property_id)
    if prop is None:
        raise AppError("not_found", "Property was not found.", 404)

    points = _collect_routing_points(session, row.service_date, cleaner_id, prop.external_id)
    snapshot = _matrix(session, clock, points)
    travel_lookup = _travel_lookup(snapshot)

    planned_end = planned_start + timedelta(minutes=task.duration_minutes)
    _other_assignments, travel_before = _check_override_feasibility(
        session,
        clock,
        cleaner_id,
        task,
        prop,
        row.service_date,
        planned_start,
        planned_end,
        row.id,
        travel_lookup,
    )

    now = clock.now()
    row.status = "superseded"
    row.version += 1
    row.updated_at = now
    replacement = Assignment(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        task_id=row.task_id,
        cleaner_id=cleaner_id,
        schedule_run_id=row.schedule_run_id,
        cleaner_schedule_version_id=row.cleaner_schedule_version_id,
        service_date=row.service_date,
        assignment_version=row.assignment_version + 1,
        planned_start=planned_start,
        planned_end=planned_end,
        duration_minutes=task.duration_minutes,
        travel_before_seconds=travel_before,
        status="published",
        is_locked=False,
        lock_reason=None,
        locked_by=None,
        acknowledged_at=None,
        completed_at=None,
        supersedes_id=row.id,
        instruction_id=row.instruction_id,
        rate_id=None,
        compensation_snapshot={},
        amount_cents=None,
        currency=row.currency,
    )
    session.add(replacement)
    bump_task(session, task)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="assignment.overridden",
        entity_type="assignment",
        entity_id=replacement.id,
        correlation_id=replacement.id,
        reason=reason,
        redacted_changes={"cleaner_id": str(cleaner_id), "version": replacement.assignment_version},
        request_id=None,
    )
    session.flush()
    return replacement


def mark_unavailable(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    cleaner_id: uuid.UUID,
    service_date: date,
    expected_version: int,
    actor: str,
) -> dict[str, object]:
    workspace_id = get_workspace_id(session)
    acquire_workspace_lock(session, workspace_id, SCHEDULE_PUBLICATION_LOCK)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{workspace_id}:availability:{cleaner_id}:{service_date}"},
    )
    availability = session.scalar(
        select(CleanerAvailability).where(
            CleanerAvailability.cleaner_id == cleaner_id,
            CleanerAvailability.service_date == service_date,
        )
    )
    if availability is None:
        effective = next(
            (row for row in effective_availability(session, service_date) if row.cleaner_id == cleaner_id), None
        )
        if effective is None:
            raise AppError("not_found", "Availability was not found.", 404)
        availability = CleanerAvailability(
            id=uuid.uuid4(),
            cleaner_id=cleaner_id,
            service_date=service_date,
            version=effective.version,
            created_at=clock.now(),
            updated_at=clock.now(),
            status="available",
            work_intervals=effective.work_intervals,
            unavailable_intervals=effective.unavailable_intervals,
            origin_label=effective.origin_label,
            origin_source="explicit",
            assumed=False,
        )
        session.add(availability)
    if availability.version != expected_version:
        raise AppError("stale_version", "Availability version is stale.", 409)
    availability.status = "unavailable"
    availability.version += 1
    availability.updated_at = clock.now()
    now = clock.now()
    rows = session.scalars(
        select(Assignment).where(
            Assignment.cleaner_id == cleaner_id,
            Assignment.service_date == service_date,
            Assignment.status.in_(_LIVE),
        )
    ).all()
    for row in rows:
        pinned = row.is_locked or row.planned_start <= now or row.status == "completed"
        row.version += 1
        row.updated_at = now
        if pinned:
            row.status = "blocked" if row.status != "completed" else row.status
            _open_exception(session, clock, str(row.task_id), "LOCK_CONFLICT" if row.is_locked else "IN_PROGRESS")
        else:
            row.status = "superseded"
            task = session.get(CleaningTask, row.task_id)
            if task is not None and task.status == "assigned":
                task.status = "ready"
                task.version += 1
                task.updated_at = now
    current_versions = session.scalars(
        select(CleanerScheduleVersion).where(
            CleanerScheduleVersion.cleaner_id == cleaner_id,
            CleanerScheduleVersion.service_date == service_date,
            CleanerScheduleVersion.status == "current",
        )
    ).all()
    for version in current_versions:
        live_left = session.scalar(
            select(Assignment.id).where(
                Assignment.cleaner_schedule_version_id == version.id,
                Assignment.status.in_(_LIVE),
            )
        )
        if live_left is None:
            version.status = "superseded"
    _bump_date(session, service_date, now)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="availability.unavailable",
        entity_type="cleaner_daily_availability",
        entity_id=availability.id,
        correlation_id=availability.id,
        reason="explicit unavailability",
        redacted_changes={"service_date": service_date.isoformat(), "version": availability.version},
        request_id=None,
    )
    session.flush()
    # NOTE: preview_schedule runs the solver while SCHEDULE_PUBLICATION_LOCK is held.
    # This is acceptable for user-initiated requests (mark_unavailable) because:
    # 1. The user is waiting for the response anyway
    # 2. The lock prevents race conditions during the read-solve-publish sequence
    # 3. For automated workers (email_automation), use gather_preview_inputs/run_preview_solver/
    #    store_preview_run to release the lock during solver execution.
    preview = preview_schedule(session, clock=clock, settings=settings, service_date=service_date, actor=actor)
    if preview.status != "preview":
        return {"replanned": False, "schedule_run_id": str(preview.id), "sms_sent": False}
    published = publish_schedule(
        session,
        clock=clock,
        settings=settings,
        run_id=preview.id,
        expected_fingerprint=preview.input_fingerprint,
        expected_day_revision=preview.day_revision,
        expected_version=preview.version,
        actor=actor,
    )
    published["replanned"] = True
    return published


def run_dispatch_clock(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    if settings.hospitable_read_only:
        raise AppError("dispatch_disabled", "Automatic local scheduling is disabled.", 403)
    assert_manual_publication(settings)
    policy = _approved_policy(session, clock)
    local = clock.now().astimezone(NY)
    if policy is None:
        return {"ran": False, "created": False, "published": False, "sms_sent": False}
    configured = str(policy.settings.get("dispatch_local_time", ""))
    hour, minute = _hour_minute(configured)
    if local.hour != hour or local.minute != minute or local.second != 0:
        return {"ran": False, "created": False, "published": False, "sms_sent": False}
    service_date = local.date() + timedelta(days=1)
    key = f"dispatch:{service_date.isoformat()}"
    existing = session.scalar(select(OutboxEvent).where(OutboxEvent.intent_key == key))
    if existing is not None:
        return {"ran": True, "created": False, "intent_key": key, "published": False, "sms_sent": False}
    event = _outbox(
        session,
        clock.now(),
        "schedule.dispatch_clock",
        uuid.uuid4(),
        1,
        {"service_date": service_date.isoformat(), "sms_sent": False, "published": False},
    )
    event.intent_key = key
    session.flush()
    return {"ran": True, "created": True, "intent_key": key, "published": False, "sms_sent": False}


def _inputs(
    session: Session,
    clock: Clock,
    service_date: date,
    preserved_assignments: tuple[Assignment, ...] = (),
) -> tuple[list[Job], list[Shift], Travel, RoutingSnapshot, list[dict[str, str]]]:
    tasks = list(
        session.scalars(
            select(CleaningTask).where(
                (CleaningTask.status.in_(("ready", "assigned", "pending_deadline")))
                | (CleaningTask.id.in_([row.task_id for row in preserved_assignments if row.status == "completed"])),
                CleaningTask.id.not_in(
                    select(CleaningOffer.task_id).where(CleaningOffer.status.in_(("open", "expired")))
                ),
            )
        ).all()
    )
    preserved_by_task = {row.task_id: row for row in preserved_assignments}
    jobs: list[Job] = []
    extra: list[dict[str, str]] = []
    now = clock.now()
    for task in tasks:
        if task.status != "completed" and departure_requires_review(session, task.id):
            extra.append({"task_id": str(task.id), "reason": "GUEST_DEPARTURE_REVIEW"})
            continue
        local_release = task.release_at.astimezone(NY).date()
        deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        # Carry unfinished work forward, without stealing an assignment from another date.
        live = _live_assignment(session, task.id)
        if live is not None and live.service_date != service_date:
            continue
        if local_release > service_date:
            continue
        preserved = preserved_by_task.get(task.id)
        completed = preserved is not None and preserved.status == "completed"
        if completed and preserved is not None:
            if task.status != "completed":
                raise AppError("preserved_assignment_conflict", "A completed assignment needs manager review.", 409)
            deadline = max(deadline, preserved.planned_end, preserved.completed_at or preserved.planned_end)
        if deadline.astimezone(NY).date() < service_date:
            if live is None:
                extra.append({"task_id": str(task.id), "reason": "DEADLINE_IMPOSSIBLE"})
            continue
        prop = session.get(Property, task.property_id)
        if prop is None:
            continue
        policy = _approved_policy(session, clock)
        if task.next_reservation_id is not None and (
            policy is None or not isinstance(policy.settings.get("readiness_buffer_minutes"), int)
        ):
            extra.append({"task_id": str(task.id), "reason": "MISSING_READINESS_BUFFER"})
            continue
        if task.duration_minutes is None:
            extra.append({"task_id": str(task.id), "reason": "MISSING_DURATION"})
            continue
        if deadline <= task.release_at or (deadline <= now and not completed):
            extra.append({"task_id": str(task.id), "reason": "DEADLINE_IMPOSSIBLE"})
            continue
        pinned = preserved_by_task.get(task.id) or _pinned_assignment(session, task.id, now)
        busy_end = None if pinned is None else max(pinned.planned_end, pinned.completed_at or pinned.planned_end)
        if (
            pinned is not None
            and not completed
            and (
                task.status != "assigned"
                or pinned.duration_minutes != task.duration_minutes
                or pinned.planned_end - pinned.planned_start != timedelta(minutes=pinned.duration_minutes)
            )
        ):
            raise AppError("preserved_assignment_conflict", "An existing assignment changed. Review the schedule.", 409)
        next_reservation = session.get(Reservation, task.next_reservation_id) if task.next_reservation_id else None
        if task.next_reservation_id is not None and next_reservation is None:
            raise AppError("reservation_missing", "Refresh reservations before scheduling.", 409)
        jobs.append(
            Job(
                task_id=str(task.id),
                place=prop.external_id,
                release=task.release_at if pinned is not None else max(task.release_at, now),
                deadline=deadline,
                duration=timedelta(minutes=task.duration_minutes)
                if pinned is None or busy_end is None
                else busy_end - pinned.planned_start,
                property_id=str(prop.id),
                locked_cleaner_id=None if pinned is None else str(pinned.cleaner_id),
                locked_start=None if pinned is None else pinned.planned_start,
                locked_end=busy_end,
                next_checkin=None if next_reservation is None else next_reservation.checkin_at,
                checkout_at=task.release_at,
                property_group=property_group(prop.name),
            )
        )
    if set(preserved_by_task) - {uuid.UUID(job.task_id) for job in jobs}:
        raise AppError("preserved_assignment_conflict", "An existing assignment cannot be preserved.", 409)
    availabilities = effective_availability(session, service_date)
    shifts: list[Shift] = []
    points = {job.place for job in jobs}
    for availability in availabilities:
        if availability.status != "available":
            continue
        cleaner = session.get(Cleaner, availability.cleaner_id)
        if cleaner is None or not cleaner.active:
            continue
        if not cleaner_ready_for_scheduling(session, cleaner.id, service_date):
            continue
        if not availability.origin_label:
            continue
        eligible = set()
        for property_id in eligible_properties(session, cleaner):
            prop = session.get(Property, property_id)
            if prop is not None:
                eligible.add(prop.external_id)
        points.add(availability.origin_label)
        start, end = _shift_bounds(availability.work_intervals)
        service_limit = availability.max_service_override or cleaner.max_service_minutes
        duty_limit = availability.max_duty_override or cleaner.max_duty_minutes
        explicit_limit = availability.max_jobs_override or cleaner.max_jobs
        max_jobs = explicit_limit or max(len(jobs), 1)
        max_cluster_jobs = None
        if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID:
            explicit_limit = min(
                (limit for limit in (availability.max_jobs_override, cleaner.max_jobs) if limit is not None),
                default=None,
            )
            max_jobs = min(explicit_limit or 3, 3)
            max_cluster_jobs = min(explicit_limit or 5, 5)
        shifts.append(
            Shift(
                cleaner_id=str(cleaner.id),
                origin=availability.origin_label,
                start=start,
                end=end,
                breaks=_breaks(availability.unavailable_intervals),
                max_jobs=max_jobs,
                max_cluster_jobs=max_cluster_jobs,
                max_service=timedelta(minutes=service_limit) if service_limit is not None else end - start,
                max_duty=timedelta(minutes=duty_limit) if duty_limit is not None else end - start,
                eligible_places=frozenset(eligible),
            )
        )
    if not points:
        points.add("synthetic:empty")
    snapshot = _matrix(session, clock, tuple(sorted(points)))
    return jobs, shifts, _travel_lookup(snapshot), snapshot, extra


def _matrix(session: Session, clock: Clock, points: tuple[str, ...]) -> RoutingSnapshot:
    baseline = session.info.get("driving_baseline")
    routed = (
        baseline.matrix(points)
        if isinstance(baseline, DrivingBaseline)
        else build_routing_service("mock").matrix(points, "bucket-30", "driving")
    )
    # Typed starting addresses never fall back to synthetic travel, even without a saved baseline.
    custom = {
        f"synthetic:start-{row.cleaner_id.hex}": row
        for row in session.scalars(select(CleanerWeeklyHours))
        if row.starting_address_ciphertext
    }
    matrix = [list(row) for row in routed.seconds_or_unreachable]
    for i, origin in enumerate(points):
        for j, destination in enumerate(points):
            if origin in custom or destination in custom:
                matrix[i][j] = 0 if i == j else None
            if origin in custom and isinstance(baseline, DrivingBaseline):
                cache = custom[origin].origin_routes or {}
                values = cache.get("seconds")
                if (
                    cache.get("status") == "ready"
                    and cache.get("baseline_id") == baseline.digest
                    and isinstance(values, dict)
                ):
                    value = values.get(destination)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        matrix[i][j] = value
    provenance = dict(routed.provenance)
    if custom:
        provenance["starting_route_cache"] = hashlib.sha256(
            json.dumps(
                {key: row.origin_routes for key, row in custom.items() if key in points},
                sort_keys=True,
            ).encode()
        ).hexdigest()
    routed = replace(routed, seconds_or_unreachable=tuple(tuple(row) for row in matrix), provenance=provenance)
    encoded = json.dumps(routed.seconds_or_unreachable)
    now = clock.now()
    row = RoutingSnapshot(
        id=uuid.uuid4(),
        created_at=now,
        matrix_digest=hashlib.sha256(encoded.encode()).hexdigest(),
        points_digest=hashlib.sha256("|".join(points).encode()).hexdigest(),
        matrix_seconds={
            "points": list(points),
            "seconds": [list(row) for row in routed.seconds_or_unreachable],
            "provenance": routed.provenance,
        },
        provider=routed.provider,
        observed_at=routed.observed_at,
        expires_at=routed.expires_at,
    )
    session.add(row)
    session.flush()
    return row


def _travel_lookup(snapshot: RoutingSnapshot) -> Travel:
    payload = snapshot.matrix_seconds
    raw_points = payload["points"]
    raw_seconds = payload["seconds"]
    if not isinstance(raw_points, list) or not isinstance(raw_seconds, list):
        raise AppError("routing_snapshot_invalid", "Routing snapshot is unreadable.", 422)
    points = [str(point) for point in raw_points]
    seconds: list[list[int | None]] = []
    for row in raw_seconds:
        if not isinstance(row, list):
            raise AppError("routing_snapshot_invalid", "Routing snapshot is unreadable.", 422)
        parsed: list[int | None] = []
        for value in row:
            if value is None:
                parsed.append(None)
            elif isinstance(value, int):
                parsed.append(value)
            else:
                raise AppError("routing_snapshot_invalid", "Routing snapshot is unreadable.", 422)
        seconds.append(parsed)

    def travel(origin: str, dest: str) -> int | None:
        if origin not in points or dest not in points:
            return None
        return seconds[points.index(origin)][points.index(dest)]

    return travel


def _fingerprint(
    session: Session,
    service_date: date,
    policy_version: int,
) -> tuple[str, ScheduleDay, dict[str, object]]:
    day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == service_date))
    now = datetime.now(UTC)
    if day is None:
        day = ScheduleDay(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            service_date=service_date,
            active_run_id=None,
            revision=1,
            policy_version=policy_version,
        )
        session.add(day)
        session.flush()
    tasks = session.scalars(select(CleaningTask).order_by(CleaningTask.id)).all()
    availability = session.scalars(
        select(CleanerAvailability).where(CleanerAvailability.service_date == service_date)
    ).all()
    revisions: dict[str, object] = {
        "cleaners": [
            {"id": str(row.id), "version": row.version, "active": row.active}
            for row in session.scalars(select(Cleaner).order_by(Cleaner.id))
        ],
        "weekly_hours": [
            {"id": str(row.cleaner_id), "version": row.version}
            for row in session.scalars(select(CleanerWeeklyHours).order_by(CleanerWeeklyHours.cleaner_id))
        ],
        "property_groups": [
            {"id": str(row.id), "version": row.version, "group": property_group(row.name)}
            for row in session.scalars(select(Property).order_by(Property.id))
        ],
        "routing_baseline": _routing_identity(session),
        "policy_version": policy_version,
        "day_revision": day.revision,
        "tasks": [{"id": str(task.id), "version": task.version} for task in tasks],
        "next_arrivals": [
            {"id": str(row.id), "version": row.version, "checkin_at": row.checkin_at.isoformat()}
            for row in session.scalars(
                select(Reservation)
                .where(Reservation.id.in_([task.next_reservation_id for task in tasks if task.next_reservation_id]))
                .order_by(Reservation.id)
            )
        ],
        "availability": [{"id": str(row.id), "version": row.version} for row in availability],
    }
    raw = json.dumps(revisions, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest(), day, revisions


def _routing_identity(session: Session) -> str:
    baseline = session.info.get("driving_baseline")
    return baseline.digest if isinstance(baseline, DrivingBaseline) else "mock-v1"


def _approved_policy(session: Session, clock: Clock) -> PolicyVersion | None:
    return session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.is_active.is_(True),
            PolicyVersion.approved_at.is_not(None),
            PolicyVersion.effective_at <= clock.now(),
        )
        .limit(1)
    )


def _shift_bounds(intervals: list[dict[str, str]]) -> tuple[datetime, datetime]:
    if not intervals:
        raise AppError("validation_error", "A dated work interval is required.", 422)
    return datetime.fromisoformat(intervals[0]["start_at"]), datetime.fromisoformat(intervals[0]["end_at"])


def _breaks(intervals: list[dict[str, str]]) -> tuple[tuple[datetime, datetime], ...]:
    return tuple(
        (datetime.fromisoformat(item["start_at"]), datetime.fromisoformat(item["end_at"])) for item in intervals
    )


def _preserved_assignments(
    session: Session,
    clock: Clock,
    service_date: date,
    assignment_ids: tuple[uuid.UUID, ...],
) -> tuple[Assignment, ...]:
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID:
        completed_ids = session.scalars(
            select(Assignment.id).where(Assignment.service_date == service_date, Assignment.status == "completed")
        )
        assignment_ids = tuple(sorted(set(assignment_ids) | set(completed_ids)))
    if not assignment_ids:
        return ()
    rows = tuple(
        session.scalars(
            select(Assignment)
            .where(Assignment.id.in_(assignment_ids))
            .order_by(Assignment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) != len(assignment_ids) or any(
        row.service_date != service_date
        or row.status not in {"planned", "published", "acknowledged", "completed"}
        or row.planned_end <= row.planned_start
        or (row.status == "completed" and row.completed_at is None)
        or (row.status != "completed" and row.planned_end <= clock.now())
        for row in rows
    ):
        raise AppError("preserved_assignment_conflict", "An existing assignment cannot be preserved.", 409)
    for row in rows:
        if (
            session.scalar(
                select(Assignment.id)
                .where(Assignment.task_id == row.task_id, Assignment.id != row.id, Assignment.status.in_(_LIVE))
                .limit(1)
            )
            is not None
        ):
            raise AppError("preserved_assignment_conflict", "An assignment has competing active work.", 409)
        if (
            row.status != "completed"
            and session.scalar(
                select(OperationalException.id)
                .where(
                    OperationalException.status.in_(("open", "acknowledged")),
                    (OperationalException.task_id == row.task_id) | (OperationalException.assignment_id == row.id),
                )
                .limit(1)
            )
            is not None
        ):
            raise AppError("preserved_assignment_conflict", "An assignment needs manager review.", 409)
    return rows


def _assignment_revision(row: Assignment) -> dict[str, object]:
    return {
        "id": str(row.id),
        "version": row.version,
        "task_id": str(row.task_id),
        "cleaner_id": str(row.cleaner_id),
        "service_date": row.service_date.isoformat(),
        "start": row.planned_start.isoformat(),
        "end": row.planned_end.isoformat(),
        "status": row.status,
        "completed_at": None if row.completed_at is None else row.completed_at.isoformat(),
        "is_locked": row.is_locked,
        "travel_before_seconds": row.travel_before_seconds,
    }


def _validate_preserved_plan(assignments: tuple[Assignment, ...], placed: tuple[Placed, ...], now: datetime) -> None:
    by_task = {item.task_id: item for item in placed}
    for row in assignments:
        item = by_task.get(str(row.task_id))
        if (
            item is None
            or item.cleaner_id != str(row.cleaner_id)
            or item.start != row.planned_start
            or item.end != max(row.planned_end, row.completed_at or row.planned_end)
            or (
                (row.status == "completed" or row.planned_start <= now)
                and int(item.travel_before.total_seconds()) != row.travel_before_seconds
            )
        ):
            raise AppError("preserved_assignment_conflict", "The plan would change an existing assignment.", 409)


def _validate_preserved_plan_from_snapshots(
    snapshots: tuple[_PreservedSnapshot, ...], placed: tuple[Placed, ...], now: datetime
) -> None:
    """Validate preserved plan using snapshot data (no DB access needed)."""
    by_task = {item.task_id: item for item in placed}
    for row in snapshots:
        item = by_task.get(row.task_id)
        expected_end = max(row.planned_end, row.completed_at or row.planned_end)
        if (
            item is None
            or item.cleaner_id != row.cleaner_id
            or item.start != row.planned_start
            or item.end != expected_end
            or (
                (row.status == "completed" or row.planned_start <= now)
                and int(item.travel_before.total_seconds()) != row.travel_before_seconds
            )
        ):
            raise AppError("preserved_assignment_conflict", "The plan would change an existing assignment.", 409)


def _supersede_replaceable(
    session: Session,
    service_date: date,
    now: datetime,
    preserved_assignment_ids: tuple[uuid.UUID, ...] = (),
) -> None:
    rows = session.scalars(
        select(Assignment).where(Assignment.service_date == service_date, Assignment.status.in_(_LIVE))
    ).all()
    for row in rows:
        if (
            row.id in preserved_assignment_ids
            or row.is_locked
            or row.planned_start <= now
            or row.status in {"completed", "blocked"}
        ):
            continue
        row.status = "superseded"
        row.version += 1
        row.updated_at = now


def _pinned_assignment(session: Session, task_id: uuid.UUID, now: datetime) -> Assignment | None:
    row = _live_assignment(session, task_id)
    if row is None:
        return None
    if row.is_locked or row.planned_start <= now:
        return row
    return None


def _live_assignment(session: Session, task_id: uuid.UUID) -> Assignment | None:
    return session.scalar(
        select(Assignment).where(Assignment.task_id == task_id, Assignment.status.in_(_LIVE)).limit(1)
    )


def _latest_assignment(session: Session, task_id: uuid.UUID) -> Assignment | None:
    return session.scalar(
        select(Assignment).where(Assignment.task_id == task_id).order_by(Assignment.assignment_version.desc()).limit(1)
    )


def _version(session: Session, now: datetime, cleaner_id: str, run: ScheduleRun) -> CleanerScheduleVersion:
    current = session.scalar(
        select(CleanerScheduleVersion)
        .where(
            CleanerScheduleVersion.cleaner_id == uuid.UUID(cleaner_id),
            CleanerScheduleVersion.service_date == run.service_date,
            CleanerScheduleVersion.status == "current",
        )
        .limit(1)
    )
    number = 1
    if current is not None:
        current.status = "superseded"
        number = current.version_number + 1
    row = CleanerScheduleVersion(
        id=uuid.uuid4(),
        created_at=now,
        cleaner_id=uuid.UUID(cleaner_id),
        service_date=run.service_date,
        schedule_run_id=run.id,
        version_number=number,
        content_digest=run.input_fingerprint,
        status="current",
    )
    session.add(row)
    session.flush()
    return row


def _ensure_instruction(
    session: Session, clock: Clock, settings: Settings, property_id: uuid.UUID, actor: str
) -> PropertyInstruction:
    row = latest_instruction(session, property_id)
    if row is not None:
        return row
    now = clock.now()
    row = PropertyInstruction(
        id=uuid.uuid4(),
        created_at=now,
        property_id=property_id,
        source_version=None,
        source_fields={},
        local_overrides={},
        source_secret_ciphertext=None,
        local_secret_ciphertext=None,
        effective_encrypted=protect_text("{}", settings.field_encryption_secret),
        effective_digest="empty",
        instruction_version=1,
        supersedes_id=None,
        author_subject=actor,
        change_reason="Empty instruction snapshot for assignment",
    )
    session.add(row)
    session.flush()
    return row


def _open_exception(session: Session, clock: Clock, task_id: str, reason: str) -> None:
    now = clock.now()
    dedupe = f"schedule:{task_id}:{reason}"
    existing = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == dedupe,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if existing is not None:
        return
    session.add(
        OperationalException(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            code=reason,
            severity="warning",
            dedupe_key=dedupe,
            status="open",
            task_id=uuid.UUID(task_id),
            assignment_id=None,
            sync_run_id=None,
            owner_subject=None,
            details={"reason": reason},
            opened_at=now,
            resolved_at=None,
            resolution_reason=None,
        )
    )


def _outbox(
    session: Session,
    now: datetime,
    topic: str,
    aggregate_id: uuid.UUID,
    version: int,
    payload: dict[str, object],
) -> OutboxEvent:
    event = OutboxEvent(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        topic=topic,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        intent_key=f"{topic}:{aggregate_id}:{version}",
        payload=payload,
        available_at=now,
        claimed_until=None,
        processed_at=None,
        attempt_count=0,
        last_error_code=None,
    )
    session.add(event)
    session.flush()
    return event


def _reject_broken_locks(jobs: list[Job], placed: tuple[Placed, ...]) -> None:
    placed_by_id = {item.task_id: item for item in placed}
    for job in jobs:
        if not job.locked_cleaner_id:
            continue
        item = placed_by_id.get(job.task_id)
        if item is None or item.cleaner_id != job.locked_cleaner_id or item.start != job.locked_start:
            raise AppError("lock_conflict", "A lock cannot publish an infeasible route.", 422)


def _bump_date(session: Session, service_date: date, now: datetime) -> None:
    from app.services.schedule_freshness import _bump

    _bump(session, service_date, now)


def _hour_minute(value: str) -> tuple[int, int]:
    hour_text, minute_text = value.split(":")
    return int(hour_text), int(minute_text)
