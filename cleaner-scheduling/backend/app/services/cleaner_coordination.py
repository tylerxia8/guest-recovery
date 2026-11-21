from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import replace
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.scheduling import Job, Placed, SolveResult, solve, validate
from app.domain.tasks import effective_deadline
from app.models import (
    Acknowledgment,
    Assignment,
    Cleaner,
    CleanerAccessLink,
    CleanerScheduleVersion,
    CleaningOffer,
    CleaningTask,
    OperationalException,
    OutboxEvent,
    Property,
    ScheduleDay,
    ScheduleRun,
)
from app.services.audit import write_audit
from app.services.cleaner_language import local_date, localized_email
from app.services.daily_availability import tomorrow_availability
from app.services.eligibility import eligible_properties
from app.services.field_cipher import reveal_text
from app.services.idempotency import request_digest
from app.services.local_routing import current_local_baseline
from app.services.property_groups import property_group
from app.services.reservation_sync import _open_exception, publication_blocked
from app.services.schedules import _approved_policy, _inputs, _matrix, _travel_lookup
from app.services.task_generation import departure_requires_review
from app.services.weekly_availability import effective_availability
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID

NY = ZoneInfo("America/New_York")
_LIVE = ("published", "acknowledged", "blocked", "completed")


def portal_token(grant_id: uuid.UUID, settings: Settings, workspace_id: uuid.UUID = LEGACY_WORKSPACE_ID) -> str:
    identity = grant_id.hex if workspace_id == LEGACY_WORKSPACE_ID else f"{workspace_id.hex}.{grant_id.hex}"
    signature = hmac.new(
        settings.field_encryption_secret.encode(), f"cleaner-portal:{identity}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{identity}.{signature}"


def issue_portal_link(session: Session, settings: Settings, clock: Clock, cleaner_id: uuid.UUID) -> str:
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("cleaner_unavailable", "This cleaner is not active.", 409)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    for grant in session.scalars(
        select(CleanerAccessLink)
        .where(
            CleanerAccessLink.cleaner_id == cleaner_id,
            CleanerAccessLink.purpose == "jobs",
            CleanerAccessLink.revoked_at.is_(None),
            CleanerAccessLink.expires_at > clock.now() + timedelta(days=1),
        )
        .order_by(CleanerAccessLink.created_at.desc())
        .with_for_update()
    ):
        token = portal_token(grant.id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
        if hmac.compare_digest(grant.token_hash, hashlib.sha256(token.encode()).hexdigest()):
            return token
    identity = uuid.uuid4()
    token = portal_token(identity, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
    session.add(
        CleanerAccessLink(
            id=identity,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            purpose="jobs",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            revoked_at=None,
        )
    )
    session.flush()
    return token


def cleaner_portal_url(session: Session, settings: Settings, clock: Clock, cleaner_id: uuid.UUID) -> str | None:
    if not settings.cleaner_portal_base_url:
        return None
    for grant in session.scalars(
        select(CleanerAccessLink)
        .where(
            CleanerAccessLink.cleaner_id == cleaner_id,
            CleanerAccessLink.purpose == "jobs",
            CleanerAccessLink.revoked_at.is_(None),
            CleanerAccessLink.expires_at > clock.now(),
        )
        .order_by(CleanerAccessLink.created_at.desc())
    ):
        token = portal_token(grant.id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
        if hmac.compare_digest(grant.token_hash, hashlib.sha256(token.encode()).hexdigest()):
            return settings.cleaner_portal_base_url.rstrip("/") + "/cleaner#" + token
    return None


def authenticate_portal(session: Session, settings: Settings, clock: Clock, token: str) -> Cleaner:
    # Hash lookup for indexing - SHA-256 collision is negligible (p < 2^-128 for < 2^64 grants).
    # The HMAC check below verifies the full token, so a hash collision alone does not grant access.
    grant = session.scalar(
        select(CleanerAccessLink).where(
            CleanerAccessLink.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            CleanerAccessLink.purpose == "jobs",
        )
    )
    cleaner = None if grant is None else session.get(Cleaner, grant.cleaner_id)
    if (
        grant is None
        or grant.revoked_at is not None
        or grant.expires_at <= clock.now()
        or cleaner is None
        or not cleaner.active
        # HMAC binding: even if a token's hash collides with another grant,
        # the full token must match HMAC(secret, "cleaner-portal:{identity}").
        # Legacy signs only grant_id; non-legacy signs {workspace_id}.{grant_id}.
        or not hmac.compare_digest(
            token, portal_token(grant.id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
        )
    ):
        raise AppError("link_unavailable", "Open the latest schedule email to use your private cleaner link.", 401)
    return cleaner


def _address(prop: Property, settings: Settings) -> str:
    if not prop.location_ciphertext:
        raise AppError("missing_destination", "The house address needs manager review.", 409)
    location = json.loads(reveal_text(prop.location_ciphertext, settings.field_encryption_secret))
    address = location.get("display_address") if isinstance(location, dict) else None
    if not isinstance(address, str) or not address.strip():
        raise AppError("missing_destination", "The house address needs manager review.", 409)
    unit = location.get("unit")
    return address.strip() + (f", unit {unit}" if isinstance(unit, str) and unit else "")


def _prior_punters(session: Session, task_id: uuid.UUID) -> set[uuid.UUID]:
    return set(session.scalars(select(CleaningOffer.original_cleaner_id).where(CleaningOffer.task_id == task_id)))


def _offer_task(session: Session, clock: Clock, offer: CleaningOffer) -> tuple[CleaningTask, Property]:
    task = session.scalar(select(CleaningTask).where(CleaningTask.id == offer.task_id).with_for_update())
    prop = None if task is None else session.get(Property, task.property_id)
    if (
        offer.status not in {"open", "expired"}
        or task is None
        or task.status != "ready"
        or departure_requires_review(session, task.id)
        or prop is None
        or not prop.active
        or effective_deadline(task.release_at, task.deadline_at, task.manager_target_at) <= clock.now()
        or session.scalar(
            select(Assignment.id).where(Assignment.task_id == offer.task_id, Assignment.status.in_(_LIVE)).limit(1)
        )
        is not None
    ):
        raise AppError("offer_unavailable", "This house is no longer available to claim.", 409)
    return task, prop


def portal_view(session: Session, settings: Settings, clock: Clock, cleaner: Cleaner) -> dict[str, object]:
    jobs: list[dict[str, object]] = []
    cutoff = clock.now().astimezone(NY).date() - timedelta(days=2)
    for assignment in session.scalars(
        select(Assignment)
        .where(
            Assignment.cleaner_id == cleaner.id,
            Assignment.status.in_(("published", "acknowledged", "completed")),
            Assignment.service_date >= cutoff,
        )
        .order_by(Assignment.planned_start, Assignment.id)
    ):
        task = session.get(CleaningTask, assignment.task_id)
        prop = None if task is None else session.get(Property, task.property_id)
        if task is None or prop is None:
            raise RuntimeError("Assignment references missing cleaning facts.")
        jobs.append(
            {
                "assignment_id": str(assignment.id),
                "version": assignment.version,
                "property_name": prop.name,
                "address": _address(prop, settings),
                "service_date": assignment.service_date.isoformat(),
                "planned_start": assignment.planned_start.isoformat(),
                "planned_end": assignment.planned_end.isoformat(),
                "deadline_at": effective_deadline(
                    task.release_at, task.deadline_at, task.manager_target_at
                ).isoformat(),
                "duration_minutes": assignment.duration_minutes,
                "status": assignment.status,
                "can_accept": assignment.status == "published",
                "can_complete": assignment.status in {"published", "acknowledged"},
                "can_punt": assignment.status in {"published", "acknowledged"}
                and not assignment.is_locked
                and clock.now() < assignment.planned_start,
            }
        )
    allowed = eligible_properties(session, cleaner)
    offers: list[dict[str, object]] = []
    for offer in session.scalars(
        select(CleaningOffer).where(CleaningOffer.status.in_(("open", "expired"))).order_by(CleaningOffer.expires_at)
    ):
        if cleaner.id in _prior_punters(session, offer.task_id):
            continue
        if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID and not any(
            row.cleaner_id == cleaner.id and row.status == "available"
            for row in effective_availability(session, offer.service_date)
        ):
            continue
        try:
            task, prop = _offer_task(session, clock, offer)
            if prop.id not in allowed:
                continue
            address = _address(prop, settings)
        except AppError:
            continue
        offers.append(
            {
                "offer_id": str(offer.id),
                "version": offer.version,
                "property_name": prop.name,
                "address": address,
                "service_date": offer.service_date.isoformat(),
                "release_at": task.release_at.isoformat(),
                "deadline_at": effective_deadline(
                    task.release_at, task.deadline_at, task.manager_target_at
                ).isoformat(),
                "duration_minutes": task.duration_minutes,
                "expires_at": offer.expires_at.isoformat(),
                "can_claim": True,
            }
        )
    return {
        "cleaner_id": str(cleaner.id),
        "cleaner_name": cleaner.display_name,
        "jobs": jobs,
        "offers": offers,
        "preferred_language": cleaner.preferred_language,
        "reading_languages": cleaner.reading_languages,
        "cleaner_version": cleaner.version,
        "availability": tomorrow_availability(session, clock, cleaner),
    }


def offer_preview(
    session: Session, settings: Settings, clock: Clock, offer_id: uuid.UUID, cleaner_id: uuid.UUID
) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    if publication_blocked(session, clock=clock, settings=settings):
        raise AppError("stale_source", "Reservation imports must be current before emailing available work.", 409)
    offer = session.scalar(select(CleaningOffer).where(CleaningOffer.id == offer_id).with_for_update())
    cleaner = session.get(Cleaner, cleaner_id)
    if offer is None or cleaner is None or not cleaner.active or not cleaner.email_ciphertext:
        raise AppError("offer_unavailable", "The offer or cleaner email is unavailable.", 409)
    task, prop = _offer_task(session, clock, offer)
    if cleaner.id in _prior_punters(session, task.id) or prop.id not in eligible_properties(session, cleaner):
        raise AppError("offer_ineligible", "This house is not available to this cleaner.", 403)
    if session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID and not any(
        row.cleaner_id == cleaner.id and row.status == "available"
        for row in effective_availability(session, offer.service_date)
    ):
        raise AppError("offer_ineligible", "Opt in to work on this date before receiving available houses.", 403)
    recipient = reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
    url = cleaner_portal_url(session, settings, clock, cleaner.id)
    deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at).astimezone(NY)
    body = (
        f"Hi {cleaner.display_name},\n\nA cleaning job is available for {offer.service_date.isoformat()}.\n\n"
        f"House: {prop.name}\nAddress: {_address(prop, settings)}\n"
        f"Ready after: {task.release_at.astimezone(NY).strftime('%b %d, %I:%M %p')} Eastern\n"
        f"Ready by: {deadline.strftime('%b %d, %I:%M %p')} Eastern\n"
        f"Estimated cleaning time: {task.duration_minutes} minutes\n\n"
        "Open your private cleaner portal to claim it. "
        "Your hours, breaks and driving time will be checked before assignment.\n"
    )
    body += (
        f"\nPlease respond by {offer.expires_at.astimezone(NY).strftime('%b %d, %I:%M %p')} Eastern. "
        "The manager is alerted if no cleaner takes it.\n"
    )
    if url is not None:
        body += f"\nYour private cleaner portal: {url}\n"
    subject, body = localized_email(
        cleaner,
        "offer",
        f"Cleaning available — {offer.service_date.isoformat()}",
        body,
        {
            "day": local_date(offer.service_date),
            "house": prop.name,
            "address": _address(prop, settings),
            "start": local_date(task.release_at),
            "due": local_date(deadline),
            "minutes": str(task.duration_minutes),
            "expires": local_date(offer.expires_at),
            "url": url or "",
        },
    )
    preview: dict[str, object] = {
        "cleaner_id": str(cleaner.id),
        "cleaner_version": cleaner.version,
        "recipient": recipient,
        "offer_id": str(offer.id),
        "subject": subject,
        "body": body,
    }
    return {
        **preview,
        "fingerprint": request_digest(
            {**preview, "offer_version": offer.version, "task_version": task.version, "property_version": prop.version}
        ),
    }


def _audit(
    session: Session, clock: Clock, cleaner: Cleaner, action: str, assignment_id: uuid.UUID, changes: dict[str, object]
) -> None:
    write_audit(
        session,
        clock=clock,
        actor_type="cleaner",
        actor_subject=f"cleaner:{cleaner.id}",
        action=action,
        entity_type="assignment",
        entity_id=assignment_id,
        correlation_id=assignment_id,
        reason=None,
        redacted_changes=changes,
        request_id=None,
    )


def _advance_day(session: Session, clock: Clock, day: date) -> None:
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == day).with_for_update())
    if schedule_day is None:
        raise RuntimeError("Published schedule day is missing.")
    previous_revision = schedule_day.revision
    schedule_day.revision += 1
    schedule_day.updated_at = clock.now()
    marker = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.intent_key == f"automatic-cleaner-plan:{day.isoformat()}")
        .with_for_update()
    )
    if (
        marker is not None
        and marker.payload.get("day_revision") == previous_revision
        and marker.payload.get("schedule_run_id") == str(schedule_day.active_run_id)
    ):
        marker.payload = {
            **marker.payload,
            "day_revision": schedule_day.revision,
            "schedule_run_id": str(schedule_day.active_run_id),
        }
        marker.version += 1
        marker.updated_at = clock.now()


def _new_version(session: Session, clock: Clock, cleaner_id: uuid.UUID, run: ScheduleRun) -> CleanerScheduleVersion:
    current = list(
        session.scalars(
            select(CleanerScheduleVersion)
            .where(
                CleanerScheduleVersion.cleaner_id == cleaner_id,
                CleanerScheduleVersion.service_date == run.service_date,
                CleanerScheduleVersion.status == "current",
            )
            .with_for_update()
        )
    )
    number = (
        int(
            session.scalar(
                select(CleanerScheduleVersion.version_number)
                .where(
                    CleanerScheduleVersion.cleaner_id == cleaner_id,
                    CleanerScheduleVersion.service_date == run.service_date,
                )
                .order_by(CleanerScheduleVersion.version_number.desc())
                .limit(1)
            )
            or 0
        )
        + 1
    )
    for version in current:
        version.status = "superseded"
    version = CleanerScheduleVersion(
        id=uuid.uuid4(),
        created_at=clock.now(),
        cleaner_id=cleaner_id,
        service_date=run.service_date,
        schedule_run_id=run.id,
        version_number=number,
        content_digest=f"portal:{uuid.uuid4().hex}",
        status="current",
    )
    session.add(version)
    session.flush()
    return version


def _validate_acceptance_route(
    session: Session, settings: Settings, clock: Clock, cleaner: Cleaner, service_date: date
) -> None:
    if current_local_baseline(session, settings) is None:
        raise AppError("local_routes_unavailable", "Saved driving routes need refreshing before accepting work.", 409)
    if _approved_policy(session, clock) is None:
        raise AppError("policy_not_approved", "Scheduling rules need manager approval.", 409)
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == service_date))
    run = (
        None
        if schedule_day is None or schedule_day.active_run_id is None
        else session.get(ScheduleRun, schedule_day.active_run_id)
    )
    if run is None or run.status != "published":
        raise AppError("schedule_unavailable", "This date no longer has a published schedule. Ask your manager.", 409)
    current = list(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.cleaner_id == cleaner.id,
                Assignment.service_date == service_date,
                Assignment.status.in_(_LIVE),
            )
            .order_by(Assignment.planned_start, Assignment.id)
            .with_for_update()
        )
    )
    _, shifts, _, _, _ = _inputs(session, clock, service_date)
    shift = next((row for row in shifts if row.cleaner_id == str(cleaner.id)), None)
    if shift is None:
        raise AppError("no_capacity", "Your hours or starting routes changed. Ask your manager to review them.", 409)
    availability = next(
        (row for row in effective_availability(session, service_date) if row.cleaner_id == cleaner.id), None
    )
    if availability is None:
        raise AppError("no_capacity", "Your working hours are unavailable for this date.", 409)
    if (
        session.info.get("workspace_id", LEGACY_WORKSPACE_ID) == LEGACY_WORKSPACE_ID
        and availability.max_jobs_override is None
        and cleaner.max_jobs is None
    ):
        shift = replace(shift, max_jobs=max(shift.max_jobs, len(current)))
    pending = [row for row in current if row.status != "completed"]
    if (
        session.scalar(
            select(OperationalException.id)
            .where(
                OperationalException.status.in_(("open", "acknowledged")),
                or_(
                    OperationalException.task_id.in_([row.task_id for row in pending]),
                    OperationalException.assignment_id.in_([row.id for row in pending]),
                ),
            )
            .limit(1)
        )
        is not None
    ):
        raise AppError("schedule_blocked", "A job in your schedule needs manager review.", 409)
    eligible_places = shift.eligible_places
    jobs: list[Job] = []
    for row in current:
        task = session.get(CleaningTask, row.task_id)
        prop = None if task is None else session.get(Property, task.property_id)
        if row.status == "blocked" or task is None or prop is None:
            raise AppError("schedule_blocked", "A job in your schedule needs manager review.", 409)
        if row.status == "completed" and row.completed_at is None:
            raise AppError("completion_missing", "A completed job is missing its finish time. Ask your manager.", 409)
        if row.status != "completed" and (
            not prop.active
            or prop.external_id not in eligible_places
            or task.status != "assigned"
            or task.duration_minutes != row.duration_minutes
            or row.planned_end - row.planned_start != timedelta(minutes=row.duration_minutes)
            or row.planned_end <= clock.now()
        ):
            raise AppError("schedule_changed", "A job in your schedule changed or expired. Ask your manager.", 409)
        busy_end = max(row.planned_end, row.completed_at or row.planned_end)
        due = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        if row.status == "completed":
            due = max(due, busy_end)
            shift = replace(shift, eligible_places=shift.eligible_places | {prop.external_id})
        jobs.append(
            Job(
                task_id=str(task.id),
                place=prop.external_id,
                release=task.release_at,
                deadline=due,
                duration=busy_end - row.planned_start,
                property_id=str(prop.id),
                property_group=property_group(prop.name),
                locked_cleaner_id=str(cleaner.id),
                locked_start=row.planned_start,
                locked_end=busy_end,
            )
        )
    snapshot = _matrix(session, clock, tuple(sorted({shift.origin, *(job.place for job in jobs)})))
    travel = _travel_lookup(snapshot)
    previous = shift.origin
    placed: list[Placed] = []
    for job, row in zip(jobs, current, strict=True):
        seconds = travel(previous, job.place)
        if seconds is None:
            raise AppError("no_route", "A driving route is unavailable. Ask your manager to review it.", 409)
        placed.append(
            Placed(
                job.task_id,
                str(cleaner.id),
                row.planned_start,
                row.planned_start + job.duration,
                timedelta(minutes=(seconds + 59) // 60),
                job.place,
            )
        )
        previous = job.place
    result = SolveResult(
        tuple(placed),
        (),
        sum(int(row.travel_before.total_seconds()) for row in placed),
        "feasible_not_proven_optimal",
        len(placed),
        "acceptance-validation-v1",
        0.0,
        {},
    )
    if validate(result, jobs, [shift], travel):
        raise AppError(
            "schedule_infeasible",
            "Your assignments no longer fit your hours, breaks or driving times. Ask your manager.",
            409,
        )
    for job, visit in zip(jobs, placed, strict=True):
        for other in session.scalars(
            select(Assignment)
            .join(CleaningTask, CleaningTask.id == Assignment.task_id)
            .where(
                CleaningTask.property_id == uuid.UUID(job.property_id),
                Assignment.cleaner_id != cleaner.id,
                Assignment.status.in_(_LIVE),
            )
        ):
            other_end = max(other.planned_end, other.completed_at or other.planned_end)
            if other.planned_start < visit.end and other_end > visit.start:
                raise AppError(
                    "house_busy", "Another cleaner is scheduled in this house at that time. Ask your manager.", 409
                )


def assignment_action(
    session: Session,
    settings: Settings,
    clock: Clock,
    cleaner: Cleaner,
    action: str,
    assignment_id: uuid.UUID,
    expected_version: int,
) -> dict[str, object]:
    assignment = session.scalar(select(Assignment).where(Assignment.id == assignment_id).with_for_update())
    if assignment is None or assignment.cleaner_id != cleaner.id:
        raise AppError("assignment_unavailable", "This assignment is not available to you.", 404)
    if assignment.version != expected_version:
        raise AppError("stale_version", "This assignment changed. Refresh your jobs.", 409)
    if assignment.status not in {"published", "acknowledged"}:
        raise AppError("assignment_unavailable", "This assignment is no longer active.", 409)
    task = session.scalar(select(CleaningTask).where(CleaningTask.id == assignment.task_id).with_for_update())
    if task is None or task.status != "assigned":
        raise AppError("assignment_changed", "The cleaning job needs manager review.", 409)
    now = clock.now()
    if action == "accept":
        if assignment.status == "acknowledged":
            return {"status": "accepted", "assignment_id": str(assignment.id)}
        prop = session.get(Property, task.property_id)
        deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        if (
            publication_blocked(session, clock=clock, settings=settings)
            or prop is None
            or not prop.active
            or prop.id not in eligible_properties(session, cleaner)
            or task.duration_minutes is None
            or assignment.duration_minutes != task.duration_minutes
            or assignment.planned_end - assignment.planned_start != timedelta(minutes=assignment.duration_minutes)
            or assignment.planned_start < task.release_at
            or assignment.planned_end > deadline
            or assignment.planned_end <= now
        ):
            raise AppError(
                "acceptance_unavailable", "This schedule changed or expired. Ask your manager to review it.", 409
            )
        if (
            session.scalar(
                select(OperationalException.id)
                .where(
                    OperationalException.status.in_(("open", "acknowledged")),
                    (OperationalException.task_id == task.id) | (OperationalException.assignment_id == assignment.id),
                )
                .limit(1)
            )
            is not None
        ):
            raise AppError("assignment_blocked", "Resolve the job's manager review before accepting it.", 409)
        if (
            session.scalar(
                select(Assignment.id)
                .where(
                    Assignment.task_id == task.id,
                    Assignment.id != assignment.id,
                    Assignment.status.in_(_LIVE),
                )
                .limit(1)
            )
            is not None
        ):
            raise AppError(
                "assignment_conflict", "This house has conflicting assignments. Ask your manager to review it.", 409
            )
        _validate_acceptance_route(session, settings, clock, cleaner, assignment.service_date)
        assignment.status = "acknowledged"
        assignment.acknowledged_at = assignment.acknowledged_at or now
        pending_acceptance = session.scalar(
            select(Assignment.id)
            .where(
                Assignment.cleaner_schedule_version_id == assignment.cleaner_schedule_version_id,
                Assignment.status.in_(_LIVE),
                Assignment.acknowledged_at.is_(None),
            )
            .limit(1)
        )
        version_accepted = session.scalar(
            select(Acknowledgment.id).where(
                Acknowledgment.cleaner_schedule_version_id == assignment.cleaner_schedule_version_id
            )
        )
        if pending_acceptance is None and version_accepted is None:
            session.add(
                Acknowledgment(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    cleaner_id=cleaner.id,
                    cleaner_schedule_version_id=assignment.cleaner_schedule_version_id,
                    sms_message_id=None,
                    actor_subject=f"cleaner:{cleaner.id}",
                    accepted_at=now,
                    method="portal",
                )
            )
        assignment.version += 1
        assignment.updated_at = now
        _audit(session, clock, cleaner, "assignment.accepted", assignment.id, {})
        return {"status": "accepted", "assignment_id": str(assignment.id)}
    if action == "complete":
        assignment.status = task.status = "completed"
        assignment.completed_at = task.completed_at = now
        assignment.version += 1
        assignment.updated_at = now
        task.completion_actor = f"cleaner:{cleaner.id}"
        task.version += 1
        task.updated_at = now
        _audit(
            session,
            clock,
            cleaner,
            "assignment.completed",
            assignment.id,
            {"accepted_before_completion": assignment.acknowledged_at is not None},
        )
        return {"status": "completed", "assignment_id": str(assignment.id)}
    if action != "punt":
        raise AppError("invalid_action", "Choose an available cleaner action.", 422)
    if assignment.is_locked or assignment.planned_start <= now:
        raise AppError("punt_unavailable", "Started or manager-locked jobs need manager help to reassign.", 409)
    assignment.status = "canceled"
    assignment.version += 1
    assignment.updated_at = now
    task.status = "ready"
    task.version += 1
    task.updated_at = now
    run = session.get(ScheduleRun, assignment.schedule_run_id)
    if run is None:
        raise RuntimeError("Assignment schedule run is missing.")
    _new_version(session, clock, cleaner.id, run)
    for previous_offer in session.scalars(
        select(CleaningOffer)
        .where(
            CleaningOffer.task_id == task.id,
            CleaningOffer.status.in_(("open", "expired")),
        )
        .with_for_update()
    ):
        previous_offer.status = "canceled"
        previous_offer.version += 1
        previous_offer.updated_at = now
        _resolve_offer_alert(session, clock, previous_offer)
    session.flush()
    offer = CleaningOffer(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        task_id=task.id,
        original_cleaner_id=cleaner.id,
        service_date=assignment.service_date,
        status="open",
        opened_at=now,
        expires_at=now + timedelta(minutes=120),
        claimed_by_cleaner_id=None,
        claimed_assignment_id=None,
    )
    session.add(offer)
    _advance_day(session, clock, assignment.service_date)
    _audit(
        session,
        clock,
        cleaner,
        "assignment.punted",
        assignment.id,
        {"offer_id": str(offer.id), "response_minutes": 120},
    )
    session.flush()
    return {"status": "punted", "assignment_id": str(assignment.id), "offer_id": str(offer.id)}


def claim_offer(
    session: Session,
    settings: Settings,
    clock: Clock,
    cleaner: Cleaner,
    offer_id: uuid.UUID,
    expected_version: int,
) -> dict[str, object]:
    if (
        publication_blocked(session, clock=clock, settings=settings)
        or current_local_baseline(session, settings) is None
    ):
        raise AppError(
            "planning_unavailable", "Current bookings and saved driving routes are required to claim work.", 409
        )
    if _approved_policy(session, clock) is None:
        raise AppError("policy_not_approved", "Scheduling rules need manager approval.", 409)
    offer = session.scalar(select(CleaningOffer).where(CleaningOffer.id == offer_id).with_for_update())
    if offer is None or offer.version != expected_version:
        raise AppError("offer_changed", "This offer changed. Refresh available work.", 409)
    task, prop = _offer_task(session, clock, offer)
    if cleaner.id in _prior_punters(session, task.id) or prop.id not in eligible_properties(session, cleaner):
        raise AppError("offer_ineligible", "This house is not available to you.", 403)
    if task.duration_minutes is None:
        raise AppError("missing_duration", "Cleaning time needs manager review.", 409)
    _unused_jobs, shifts, _unused_travel, _unused_snapshot, _extra = _inputs(session, clock, offer.service_date)
    shift = next((item for item in shifts if item.cleaner_id == str(cleaner.id)), None)
    if shift is None:
        raise AppError("no_capacity", "Set working hours and a verified starting location before claiming work.", 409)
    current = list(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.cleaner_id == cleaner.id,
                Assignment.service_date == offer.service_date,
                Assignment.status.in_(_LIVE),
            )
            .order_by(Assignment.planned_start)
            .with_for_update()
        )
    )
    jobs: list[Job] = []
    for existing in current:
        own_task = session.get(CleaningTask, existing.task_id)
        own_property = None if own_task is None else session.get(Property, own_task.property_id)
        if existing.status == "blocked" or own_task is None or own_property is None:
            raise AppError("schedule_blocked", "Resolve your current schedule problem before claiming more work.", 409)
        if existing.status != "completed" and existing.planned_start <= clock.now():
            raise AppError(
                "job_in_progress", "Mark your started job done or ask your manager before claiming more work.", 409
            )
        if existing.status == "completed" and existing.completed_at is None:
            raise AppError(
                "completion_missing", "Your completed job needs manager review before claiming more work.", 409
            )
        if existing.status != "completed" and (
            own_task.status != "assigned" or own_task.duration_minutes != existing.duration_minutes
        ):
            raise AppError("schedule_changed", "Your current cleaning times need manager review.", 409)
        busy_end = max(existing.planned_end, existing.completed_at or existing.planned_end)
        due = effective_deadline(own_task.release_at, own_task.deadline_at, own_task.manager_target_at)
        if existing.status == "completed":
            due = max(due, busy_end)
        jobs.append(
            Job(
                task_id=str(own_task.id),
                place=own_property.external_id,
                release=own_task.release_at,
                deadline=due,
                duration=busy_end - existing.planned_start,
                property_id=str(own_task.property_id),
                property_group=property_group(own_property.name),
                locked_cleaner_id=str(cleaner.id),
                locked_start=existing.planned_start,
                locked_end=busy_end,
            )
        )
    jobs.append(
        Job(
            task_id=str(task.id),
            place=prop.external_id,
            release=max(task.release_at, clock.now()),
            deadline=effective_deadline(task.release_at, task.deadline_at, task.manager_target_at),
            duration=timedelta(minutes=task.duration_minutes),
            property_id=str(prop.id),
            property_group=property_group(prop.name),
        )
    )
    availability = next(
        (row for row in effective_availability(session, offer.service_date) if row.cleaner_id == cleaner.id), None
    )
    if availability is None:
        raise AppError("no_capacity", "Your hours are unavailable for this date.", 409)
    if (
        session.info.get("workspace_id", LEGACY_WORKSPACE_ID) == LEGACY_WORKSPACE_ID
        and availability.max_jobs_override is None
        and cleaner.max_jobs is None
    ):
        shift = replace(shift, max_jobs=max(shift.max_jobs, len(jobs)))
    points = tuple(sorted({shift.origin, *(job.place for job in jobs)}))
    snapshot = _matrix(session, clock, points)
    travel = _travel_lookup(snapshot)
    result = solve(jobs, [shift], travel, time_limit_seconds=min(settings.solver_time_limit_seconds, 10))
    if validate(result, jobs, [shift], travel) or len(result.placed) != len(jobs):
        raise AppError(
            "no_feasible_claim", "This house does not fit your hours, breaks, driving time and existing jobs.", 409
        )
    placement = next((item for item in result.placed if item.task_id == str(task.id)), None)
    if placement is None:
        raise AppError("no_feasible_claim", "This house does not fit your current schedule.", 409)
    for other in session.scalars(
        select(Assignment)
        .join(CleaningTask, CleaningTask.id == Assignment.task_id)
        .where(
            CleaningTask.property_id == prop.id,
            Assignment.status.in_(_LIVE),
            or_(
                and_(Assignment.planned_start < placement.end, Assignment.planned_end > placement.start),
                and_(
                    Assignment.status.in_(("published", "acknowledged", "blocked")),
                    Assignment.planned_start <= clock.now(),
                ),
            ),
        )
    ):
        if other.task_id != task.id:
            raise AppError("house_busy", "Another cleaner is already scheduled in this house at that time.", 409)
    schedule_day = session.scalar(
        select(ScheduleDay).where(ScheduleDay.service_date == offer.service_date).with_for_update()
    )
    run = (
        None
        if schedule_day is None or schedule_day.active_run_id is None
        else session.get(ScheduleRun, schedule_day.active_run_id)
    )
    if run is None or run.status != "published":
        raise AppError("schedule_unavailable", "This date needs a published schedule before claiming work.", 409)
    now = clock.now()
    version = _new_version(session, clock, cleaner.id, run)
    original = session.scalar(
        select(Assignment).where(Assignment.task_id == task.id).order_by(Assignment.assignment_version.desc()).limit(1)
    )
    if original is None:
        raise RuntimeError("Offered task has no assignment history.")
    assignment = Assignment(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        task_id=task.id,
        cleaner_id=cleaner.id,
        schedule_run_id=run.id,
        cleaner_schedule_version_id=version.id,
        service_date=offer.service_date,
        assignment_version=original.assignment_version + 1,
        planned_start=placement.start,
        planned_end=placement.end,
        duration_minutes=task.duration_minutes,
        travel_before_seconds=int(placement.travel_before.total_seconds()),
        status="acknowledged",
        is_locked=False,
        lock_reason=None,
        locked_by=None,
        acknowledged_at=now,
        completed_at=None,
        supersedes_id=original.id,
        instruction_id=original.instruction_id,
        rate_id=None,
        compensation_snapshot={},
        amount_cents=None,
        currency=original.currency,
    )
    session.add(assignment)
    session.flush()
    placed_by_task = {item.task_id: item for item in result.placed}
    for existing in current:
        seconds = int(placed_by_task[str(existing.task_id)].travel_before.total_seconds())
        if existing.travel_before_seconds != seconds:
            _audit(
                session,
                clock,
                cleaner,
                "assignment.route_updated",
                existing.id,
                {"previous_seconds": existing.travel_before_seconds, "seconds": seconds},
            )
            existing.travel_before_seconds = seconds
            existing.version += 1
            existing.updated_at = now
    task.status = "assigned"
    task.version += 1
    task.updated_at = now
    offer.status = "claimed"
    offer.claimed_by_cleaner_id = cleaner.id
    offer.claimed_assignment_id = assignment.id
    offer.version += 1
    offer.updated_at = now
    session.add(
        Acknowledgment(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            cleaner_id=cleaner.id,
            cleaner_schedule_version_id=version.id,
            sms_message_id=None,
            actor_subject=f"cleaner:{cleaner.id}",
            accepted_at=now,
            method="portal",
        )
    )
    _advance_day(session, clock, offer.service_date)
    _resolve_offer_alert(session, clock, offer)
    _audit(session, clock, cleaner, "assignment.claimed", assignment.id, {"offer_id": str(offer.id)})
    session.flush()
    return {"status": "claimed", "assignment_id": str(assignment.id), "offer_id": str(offer.id)}


def _resolve_offer_alert(session: Session, clock: Clock, offer: CleaningOffer) -> None:
    for row in session.scalars(
        select(OperationalException).where(
            OperationalException.dedupe_key == f"cleaner-offer-unclaimed:{offer.id}",
            OperationalException.status.in_(("open", "acknowledged")),
        )
    ):
        row.status = "resolved"
        row.resolved_at = row.updated_at = clock.now()
        row.resolution_reason = "The cleaning job is covered or no longer requires reassignment."
        row.version += 1


def process_offer_deadlines(session: Session, settings: Settings, clock: Clock) -> int:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    alerted = 0
    for offer in session.scalars(
        select(CleaningOffer).where(CleaningOffer.status.in_(("open", "expired"))).with_for_update()
    ):
        task = session.get(CleaningTask, offer.task_id)
        covered = session.scalar(
            select(Assignment.id).where(Assignment.task_id == offer.task_id, Assignment.status.in_(_LIVE)).limit(1)
        )
        if task is None or task.status in {"completed", "canceled"} or covered is not None:
            offer.status = "canceled"
            offer.version += 1
            offer.updated_at = clock.now()
            _resolve_offer_alert(session, clock, offer)
            continue
        if offer.status == "open" and offer.expires_at <= clock.now():
            offer.status = "expired"
            offer.version += 1
            offer.updated_at = clock.now()
            prop = session.get(Property, task.property_id)
            _open_exception(
                session,
                clock=clock,
                code="CLEANING_UNCLAIMED",
                dedupe_key=f"cleaner-offer-unclaimed:{offer.id}",
                sync_run_id=None,
                details={
                    "offer_id": str(offer.id),
                    "task_id": str(task.id),
                    "property_name": None if prop is None else prop.name,
                    "service_date": offer.service_date.isoformat(),
                    "response_minutes": 120,
                },
                severity="warning",
            )
            alerted += 1
    session.flush()
    return alerted
