from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import smtplib
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models import (
    Assignment,
    Cleaner,
    CleanerScheduleVersion,
    CleaningOffer,
    CleaningTask,
    EmailDelivery,
    OperationalException,
    OutboxEvent,
    ScheduleDay,
    ScheduleRun,
)
from app.services.assignment_email import assignment_preview, reservation_change_preview
from app.services.audit import write_audit
from app.services.cleaner_coordination import issue_portal_link, offer_preview
from app.services.field_cipher import protect_text, reveal_text
from app.services.gmail import connect, send_connected_email
from app.services.local_routing import current_local_baseline
from app.services.onboarding_email import onboarding_preview
from app.services.reservation_sync import _open_exception, publication_blocked
from app.services.schedules import (
    gather_preview_inputs,
    publish_schedule,
    run_preview_solver,
    store_preview_run,
)
from app.services.weekly_availability import cleaner_ready_for_scheduling
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
_ACTOR = "local-email-automation"


def _problem(session: Session, clock: Clock, code: str, key: str, details: dict[str, object]) -> None:
    _open_exception(
        session, clock=clock, code=code, dedupe_key=key, sync_run_id=None, details=details, severity="warning"
    )


def _resolve(session: Session, clock: Clock, key: str) -> None:
    for row in session.scalars(
        select(OperationalException).where(
            OperationalException.dedupe_key == key, OperationalException.status.in_(("open", "acknowledged"))
        )
    ):
        row.status = "resolved"
        row.resolved_at = row.updated_at = clock.now()
        row.resolution_reason = "Automatic email requirements are now satisfied."
        row.version += 1


@dataclass
class _PlanningCheck:
    """Result of checking whether a day needs planning."""

    needs_planning: bool
    skip_reason: str | None
    preserved_ids: tuple[uuid.UUID, ...]
    schedule_day_id: uuid.UUID | None
    active_run_id: uuid.UUID | None


def _check_planning_needed(session: Session, clock: Clock, day: date) -> _PlanningCheck:
    """Check whether a day needs planning (runs under lock).

    Returns whether planning is needed and the preserved assignment IDs.
    Does NOT run the solver.
    """
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == day).with_for_update())
    active = (
        None
        if schedule_day is None or schedule_day.active_run_id is None
        else session.get(ScheduleRun, schedule_day.active_run_id)
    )
    marker_key = f"automatic-cleaner-plan:{day.isoformat()}"
    marker = session.scalar(select(OutboxEvent).where(OutboxEvent.intent_key == marker_key).with_for_update())
    preserved_ids: tuple[uuid.UUID, ...] = ()
    replan = False
    if active is not None and active.status == "published":
        if marker is None or marker.payload.get("schedule_run_id") != str(active.id):
            _problem(
                session,
                clock,
                "AUTOMATIC_SCHEDULE_REVIEW",
                marker_key,
                {"service_date": day.isoformat(), "reason": "The published schedule requires manager review."},
            )
            return _PlanningCheck(
                needs_planning=False,
                skip_reason="review_required",
                preserved_ids=(),
                schedule_day_id=schedule_day.id if schedule_day else None,
                active_run_id=active.id,
            )
        if schedule_day is not None and marker.payload.get("day_revision") != schedule_day.revision:
            replan = day >= clock.now().astimezone(NY).date()
            if replan:
                preserved_ids = tuple(
                    session.scalars(
                        select(Assignment.id)
                        .where(
                            Assignment.service_date == day,
                            Assignment.status.in_(("planned", "published", "acknowledged", "blocked", "completed")),
                        )
                        .order_by(Assignment.id)
                    )
                )
    needs_planning = active is None or active.status != "published" or replan
    if needs_planning and marker is not None and not replan:
        _problem(
            session,
            clock,
            "AUTOMATIC_SCHEDULE_REVIEW",
            marker_key,
            {"service_date": day.isoformat(), "reason": "Published schedule is unavailable."},
        )
        return _PlanningCheck(
            needs_planning=False,
            skip_reason="unavailable",
            preserved_ids=(),
            schedule_day_id=schedule_day.id if schedule_day else None,
            active_run_id=active.id if active else None,
        )
    return _PlanningCheck(
        needs_planning=needs_planning,
        skip_reason=None,
        preserved_ids=preserved_ids,
        schedule_day_id=schedule_day.id if schedule_day else None,
        active_run_id=active.id if active else None,
    )


def _run_solver_and_publish(
    factory: sessionmaker[Session], settings: Settings, clock: Clock, day: date, preserved_ids: tuple[uuid.UUID, ...]
) -> ScheduleRun:
    """Run the solver for a day and publish immediately using split transactions.

    1. Gather inputs under lock, then commit to release lock
    2. Run solver (CPU-bound, no DB access)
    3. Store result and publish under lock in new transaction

    This prevents holding the lock during solver execution.
    """
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        inputs = gather_preview_inputs(
            session,
            clock=clock,
            settings=settings,
            service_date=day,
            actor=_ACTOR,
            preserved_assignment_ids=preserved_ids,
        )
        session.commit()

    result, started, finished = run_preview_solver(inputs, clock)

    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        run = store_preview_run(
            session,
            clock=clock,
            settings=settings,
            inputs=inputs,
            result=result,
            started=started,
            finished=finished,
        )
        if run.status != "preview":
            raise AppError("infeasible", "Independent validation rejected automatic planning.", 409)
        publish_schedule(
            session,
            clock=clock,
            settings=settings,
            run_id=run.id,
            expected_fingerprint=run.input_fingerprint,
            expected_day_revision=run.day_revision,
            expected_version=run.version,
            actor=_ACTOR,
        )
        session.commit()
    return run


def _finalize_planning(session: Session, clock: Clock, day: date, run: ScheduleRun | None) -> bool:
    """Finalize planning for a day after solver has run (runs under lock).

    Creates/updates the marker and resolves the problem if any.
    Returns True if the day is ready for email queuing.
    """
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == day).with_for_update())
    if schedule_day is None:
        raise RuntimeError("Published schedule day is missing.")
    active_id = run.id if run else schedule_day.active_run_id
    if active_id is None:
        raise RuntimeError("No active run after planning.")
    active = session.get(ScheduleRun, active_id)
    if active is None:
        raise RuntimeError("Active run is missing.")
    marker_key = f"automatic-cleaner-plan:{day.isoformat()}"
    marker = session.scalar(select(OutboxEvent).where(OutboxEvent.intent_key == marker_key).with_for_update())
    if (
        marker is not None
        and marker.payload.get("schedule_run_id") == str(active.id)
        and marker.payload.get("day_revision") != schedule_day.revision
    ):
        _problem(
            session,
            clock,
            "AUTOMATIC_SCHEDULE_REVIEW",
            marker_key,
            {"service_date": day.isoformat(), "reason": "Scheduling inputs changed after publication."},
        )
        return False
    now = clock.now()
    payload: dict[str, object] = {
        "schedule_run_id": str(active.id),
        "day_revision": schedule_day.revision,
        "service_date": day.isoformat(),
    }
    if marker is None:
        marker = OutboxEvent(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            topic="local.email_schedule",
            aggregate_id=active.id,
            aggregate_version=active.version,
            intent_key=marker_key,
            payload=payload,
            available_at=now,
            claimed_until=None,
            processed_at=now,
            attempt_count=1,
            last_error_code=None,
        )
        session.add(marker)
    elif marker.payload != payload:
        marker.payload = payload
        marker.aggregate_id = active.id
        marker.aggregate_version = active.version
        marker.updated_at = now
        marker.version += 1
    _resolve(session, clock, marker_key)
    return True


def _queue(session: Session, settings: Settings, clock: Clock, day: date) -> int:
    queued = 0
    versions = list(
        session.scalars(
            select(CleanerScheduleVersion)
            .where(CleanerScheduleVersion.service_date == day, CleanerScheduleVersion.status == "current")
            .with_for_update()
        )
    )
    for version in versions:
        cleaner = session.get(Cleaner, version.cleaner_id)
        if cleaner is None or not cleaner.active:
            continue
        if (
            settings.workspace_signup_enabled
            and session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID
            and not cleaner_ready_for_scheduling(session, cleaner.id, day)
        ):
            continue
        previous_delivery = session.scalar(
            select(EmailDelivery)
            .where(
                EmailDelivery.cleaner_id == cleaner.id,
                EmailDelivery.service_date == day,
                EmailDelivery.kind == "assignment",
                EmailDelivery.status.in_(("accepted", "uncertain")),
            )
            .order_by(EmailDelivery.created_at.desc(), EmailDelivery.id.desc())
            .limit(1)
        )
        local = clock.now().astimezone(NY)
        if (
            previous_delivery is None
            and day > local.date()
            and (day != local.date() + timedelta(days=1) or local.strftime("%H:%M") < settings.dispatch_local_time)
        ):
            continue
        if (
            session.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.topic == "cleaning.reservation_changed",
                    OutboxEvent.processed_at.is_(None),
                    OutboxEvent.payload["cleaner_id"].astext == str(cleaner.id),
                    OutboxEvent.payload["service_date"].astext == day.isoformat(),
                )
                .limit(1)
            )
            is not None
        ):
            continue
        issue_key = f"cleaner-email:{cleaner.id}:{day.isoformat()}"
        pending = session.scalar(
            select(Assignment.id)
            .where(
                Assignment.cleaner_id == version.cleaner_id,
                Assignment.service_date == version.service_date,
                Assignment.status.in_(("published", "acknowledged")),
                Assignment.planned_start > clock.now(),
            )
            .limit(1)
        )
        if pending is None:
            _resolve(session, clock, issue_key)
            continue
        if not cleaner.email_ciphertext:
            _problem(
                session,
                clock,
                "CLEANER_EMAIL_MISSING",
                issue_key,
                {"cleaner_id": str(cleaner.id), "cleaner_name": cleaner.display_name, "service_date": day.isoformat()},
            )
            continue
        try:
            issue_portal_link(session, settings, clock, cleaner.id)
            preview = assignment_preview(session, settings, clock, cleaner.id, day)
        except AppError as error:
            _problem(
                session,
                clock,
                "ASSIGNMENT_EMAIL_BLOCKED",
                issue_key,
                {
                    "cleaner_id": str(cleaner.id),
                    "cleaner_name": cleaner.display_name,
                    "service_date": day.isoformat(),
                    "reason_code": error.code,
                },
            )
            continue
        _resolve(session, clock, issue_key)
        previous_snapshot = (
            json.loads(reveal_text(previous_delivery.snapshot_ciphertext, settings.field_encryption_secret))
            if previous_delivery is not None
            else None
        )
        unchanged = (
            previous_snapshot is not None
            and previous_snapshot.get("recipient") == preview["recipient"]
            and (previous_snapshot.get("language") or "en") == (preview.get("language") or "en")
            and (
                previous_snapshot.get(
                    "semantic_body", previous_snapshot.get("schedule_body", previous_snapshot.get("body"))
                )
                == preview.get("semantic_body", preview["body"])
            )
        )
        if not unchanged and previous_snapshot is not None:
            previous_content = previous_snapshot.get("assignment_content")
            current_content = preview.get("assignment_content")
            if isinstance(previous_content, dict) and isinstance(current_content, dict):
                removed = set(previous_content) - set(current_content)
                unchanged = (
                    bool(removed)
                    and previous_snapshot.get("recipient") == preview["recipient"]
                    and (previous_snapshot.get("language") or "en") == (preview.get("language") or "en")
                    and all(previous_content.get(key) == value for key, value in current_content.items())
                    and all(
                        (row := session.get(Assignment, uuid.UUID(assignment_id))) is not None
                        and row.cleaner_id == cleaner.id
                        and row.service_date == day
                        and (
                            row.status == "completed"
                            or (
                                row.status in {"published", "acknowledged"}
                                and row.planned_start <= clock.now() < row.planned_end
                            )
                        )
                        for assignment_id in removed
                    )
                )
        for pending_delivery in session.scalars(
            select(EmailDelivery)
            .where(
                EmailDelivery.cleaner_id == cleaner.id,
                EmailDelivery.service_date == day,
                EmailDelivery.kind == "assignment",
                EmailDelivery.status == "queued",
            )
            .with_for_update()
        ):
            snapshot = json.loads(reveal_text(pending_delivery.snapshot_ciphertext, settings.field_encryption_secret))
            if snapshot.get("notification") != "reservation_change" and (
                unchanged or snapshot.get("body") != preview["body"]
            ):
                pending_delivery.status = "stale"
                pending_delivery.error_code = "superseded_schedule"
                pending_delivery.updated_at = clock.now()
                pending_delivery.version += 1
        if unchanged:
            continue
        semantic = {
            "cleaner_id": str(cleaner.id),
            "service_date": day.isoformat(),
            "recipient": preview["recipient"],
            "subject": re.sub(r" — V\d+$", "", str(preview["subject"])),
            "body": preview["body"],
        }
        if previous_delivery is not None:
            semantic["previous_delivery_id"] = str(previous_delivery.id)
        key = hmac.new(
            settings.field_encryption_secret.encode(), json.dumps(semantic, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        existing = session.scalar(select(EmailDelivery).where(EmailDelivery.message_key == key).with_for_update())
        now = clock.now()
        if existing is not None:
            if existing.status in {"queued", "stale"}:
                if existing.status == "stale":
                    existing.status = "queued"
                    existing.attempt_count = 0
                    existing.next_attempt_at = now
                    existing.claimed_at = None
                    existing.error_code = None
                    _resolve(session, clock, f"email-delivery:{existing.id}")
                existing.fingerprint = str(preview["fingerprint"])
                existing.snapshot_ciphertext = protect_text(
                    json.dumps(preview, sort_keys=True), settings.field_encryption_secret
                )
                existing.cleaner_schedule_version_id = version.id
                existing.updated_at = now
                existing.version += 1
            continue
        delivery = EmailDelivery(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=cleaner.id,
            cleaner_schedule_version_id=version.id,
            service_date=day,
            message_key=key,
            fingerprint=str(preview["fingerprint"]),
            snapshot_ciphertext=protect_text(json.dumps(preview, sort_keys=True), settings.field_encryption_secret),
            status="queued",
            attempt_count=0,
            next_attempt_at=now,
            claimed_at=None,
            accepted_at=None,
            error_code=None,
        )
        session.add(delivery)
        write_audit(
            session,
            clock=clock,
            actor_type="system",
            actor_subject=_ACTOR,
            action="email.queued",
            entity_type="email_delivery",
            entity_id=delivery.id,
            correlation_id=delivery.id,
            reason=None,
            redacted_changes={"cleaner_id": str(cleaner.id), "service_date": day.isoformat()},
            request_id=None,
        )
        queued += 1
    return queued


def _queue_reservation_changes(session: Session, settings: Settings, clock: Clock) -> int:
    queued = 0
    events = list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.topic == "cleaning.reservation_changed", OutboxEvent.processed_at.is_(None))
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update()
        )
    )
    for event in events:
        latest = session.scalar(
            select(OutboxEvent.id)
            .where(
                OutboxEvent.topic == "cleaning.reservation_changed",
                OutboxEvent.aggregate_id == event.aggregate_id,
            )
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .limit(1)
        )
        if latest != event.id:
            event.processed_at = clock.now()
            obsolete_key = hmac.new(
                settings.field_encryption_secret.encode(), f"reservation-change:{event.id}".encode(), hashlib.sha256
            ).hexdigest()
            obsolete = session.scalar(
                select(EmailDelivery).where(EmailDelivery.message_key == obsolete_key).with_for_update()
            )
            if obsolete is not None and obsolete.status in {"queued", "stale"}:
                obsolete.status = "stale"
                obsolete.error_code = "superseded_reservation_change"
                obsolete.updated_at = clock.now()
                obsolete.version += 1
            continue
        assignment = session.get(Assignment, event.aggregate_id)
        if assignment is None:
            raise RuntimeError("Reservation change references a missing assignment.")
        previous = None
        for sent in session.scalars(
            select(EmailDelivery)
            .where(
                EmailDelivery.cleaner_id == assignment.cleaner_id,
                EmailDelivery.service_date == assignment.service_date,
                EmailDelivery.kind == "assignment",
                EmailDelivery.created_at <= event.created_at,
                EmailDelivery.status.in_(("accepted", "uncertain")),
            )
            .order_by(EmailDelivery.created_at.desc(), EmailDelivery.id.desc())
        ):
            sent_snapshot = json.loads(reveal_text(sent.snapshot_ciphertext, settings.field_encryption_secret))
            task_ids = sent_snapshot.get("task_ids")
            if (isinstance(task_ids, list) and str(assignment.task_id) in task_ids) or (
                task_ids is None
                and assignment.created_at <= sent.created_at
                and sent_snapshot.get("notification") != "reservation_change"
            ):
                previous = sent
                break
        if previous is None:
            sending = session.scalar(
                select(EmailDelivery.id)
                .where(
                    EmailDelivery.cleaner_id == assignment.cleaner_id,
                    EmailDelivery.service_date == assignment.service_date,
                    EmailDelivery.kind == "assignment",
                    EmailDelivery.status == "sending",
                    EmailDelivery.created_at >= assignment.created_at,
                    EmailDelivery.created_at <= event.created_at,
                )
                .limit(1)
            )
            if sending is None:
                event.processed_at = clock.now()
            continue
        key = hmac.new(
            settings.field_encryption_secret.encode(), f"reservation-change:{event.id}".encode(), hashlib.sha256
        ).hexdigest()
        delivery = session.scalar(select(EmailDelivery).where(EmailDelivery.message_key == key).with_for_update())
        if delivery is not None and delivery.status in {"accepted", "uncertain", "sending", "failed"}:
            event.processed_at = clock.now()
            continue
        try:
            preview = reservation_change_preview(session, settings, clock, event.id)
        except AppError as error:
            _problem(
                session,
                clock,
                "ASSIGNMENT_EMAIL_BLOCKED",
                f"reservation-change-email:{event.id}",
                {
                    "cleaner_id": str(assignment.cleaner_id),
                    "service_date": assignment.service_date.isoformat(),
                    "reason_code": error.code,
                },
            )
            continue
        previous_snapshot = json.loads(reveal_text(previous.snapshot_ciphertext, settings.field_encryption_secret))
        if (
            previous_snapshot.get("notification") == "reservation_change"
            and previous_snapshot.get("body") == preview["body"]
            and previous_snapshot.get("recipient") == preview["recipient"]
        ):
            event.processed_at = clock.now()
            continue
        now = clock.now()
        if delivery is None:
            delivery = EmailDelivery(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                cleaner_id=assignment.cleaner_id,
                cleaner_schedule_version_id=assignment.cleaner_schedule_version_id,
                service_date=assignment.service_date,
                message_key=key,
                fingerprint=str(preview["fingerprint"]),
                snapshot_ciphertext=protect_text(json.dumps(preview, sort_keys=True), settings.field_encryption_secret),
                status="queued",
                attempt_count=0,
                next_attempt_at=now,
                claimed_at=None,
                accepted_at=None,
                error_code=None,
            )
            session.add(delivery)
            queued += 1
            write_audit(
                session,
                clock=clock,
                actor_type="system",
                actor_subject=_ACTOR,
                action="email.reservation_change_queued",
                entity_type="email_delivery",
                entity_id=delivery.id,
                correlation_id=delivery.id,
                reason=None,
                redacted_changes={"cleaner_id": str(assignment.cleaner_id), "event_id": str(event.id)},
                request_id=None,
            )
        else:
            if delivery.status == "stale":
                delivery.attempt_count = 0
                delivery.next_attempt_at = now
                delivery.claimed_at = None
                delivery.error_code = None
            delivery.status = "queued"
            delivery.fingerprint = str(preview["fingerprint"])
            delivery.snapshot_ciphertext = protect_text(
                json.dumps(preview, sort_keys=True), settings.field_encryption_secret
            )
            delivery.updated_at = now
            delivery.version += 1
        _resolve(session, clock, f"reservation-change-email:{event.id}")
    return queued


def _queue_offers(session: Session, settings: Settings, clock: Clock) -> int:
    queued = 0
    offers = list(
        session.scalars(
            select(CleaningOffer)
            .where(CleaningOffer.status == "open", CleaningOffer.expires_at > clock.now())
            .with_for_update()
        )
    )
    cleaners = list(
        session.scalars(select(Cleaner).where(Cleaner.active.is_(True), Cleaner.email_ciphertext.is_not(None)))
    )
    for offer in offers:
        for cleaner in cleaners:
            if (
                settings.workspace_signup_enabled
                and session.info.get("workspace_id", LEGACY_WORKSPACE_ID) != LEGACY_WORKSPACE_ID
                and not cleaner_ready_for_scheduling(session, cleaner.id, offer.service_date)
            ):
                continue
            try:
                issue_portal_link(session, settings, clock, cleaner.id)
                preview = offer_preview(session, settings, clock, offer.id, cleaner.id)
            except AppError:
                continue
            semantic = {
                "kind": "offer",
                "offer_id": str(offer.id),
                "cleaner_id": str(cleaner.id),
                "recipient": preview["recipient"],
                "body": preview["body"],
            }
            key = hmac.new(
                settings.field_encryption_secret.encode(), json.dumps(semantic, sort_keys=True).encode(), hashlib.sha256
            ).hexdigest()
            row = session.scalar(select(EmailDelivery).where(EmailDelivery.message_key == key).with_for_update())
            now = clock.now()
            if row is not None:
                if row.status in {"queued", "stale"}:
                    if row.status == "stale":
                        row.status = "queued"
                        row.attempt_count = 0
                        row.next_attempt_at = now
                        row.claimed_at = None
                        row.error_code = None
                    row.fingerprint = str(preview["fingerprint"])
                    row.snapshot_ciphertext = protect_text(
                        json.dumps(preview, sort_keys=True), settings.field_encryption_secret
                    )
                    row.updated_at = now
                continue
            row = EmailDelivery(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                cleaner_id=cleaner.id,
                cleaner_schedule_version_id=None,
                kind="offer",
                offer_id=offer.id,
                service_date=offer.service_date,
                message_key=key,
                fingerprint=str(preview["fingerprint"]),
                snapshot_ciphertext=protect_text(json.dumps(preview, sort_keys=True), settings.field_encryption_secret),
                status="queued",
                attempt_count=0,
                next_attempt_at=now,
                claimed_at=None,
                accepted_at=None,
                error_code=None,
            )
            session.add(row)
            write_audit(
                session,
                clock=clock,
                actor_type="system",
                actor_subject=_ACTOR,
                action="email.offer_queued",
                entity_type="email_delivery",
                entity_id=row.id,
                correlation_id=row.id,
                reason=None,
                redacted_changes={"cleaner_id": str(cleaner.id), "offer_id": str(offer.id)},
                request_id=None,
            )
            queued += 1
    return queued


def _drain(
    factory: sessionmaker[Session],
    settings: Settings,
    clock: Clock,
    *,
    kinds: tuple[str, ...] = ("assignment", "offer"),
) -> dict[str, int]:
    """Drain email delivery queue.

    Lock strategy: The advisory lock is NOT held during SMTP I/O. Instead:
    1. Claim phase: Take lock, set status='sending' (the claim), commit (releases lock)
    2. Validation phase: Take lock, run all validation checks, extract send params, commit (releases lock)
    3. Send phase: Do SMTP I/O with no lock and no open transaction
    4. Record phase: Take lock, re-check status/version, record result, commit

    The 'sending' status acts as the claim. If we crash during send, the stranded
    scan at the top marks it 'uncertain' after 5 minutes.
    """
    counts = {"accepted": 0, "uncertain": 0, "blocked": 0}
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        for stranded in session.scalars(
            select(EmailDelivery)
            .where(
                EmailDelivery.kind.in_(kinds),
                EmailDelivery.status == "sending",
                EmailDelivery.claimed_at < clock.now() - timedelta(minutes=5),
            )
            .with_for_update()
        ):
            if stranded.submitting_at is None:
                if stranded.attempt_count < 3:
                    stranded.status = "queued"
                    stranded.error_code = "interrupted_before_send"
                    stranded.next_attempt_at = clock.now() + timedelta(minutes=1)
                    stranded.updated_at = clock.now()
                    stranded.version += 1
                    logger.warning(
                        "email_automation.stranded_requeued delivery_id=%s kind=%s",
                        stranded.id,
                        stranded.kind,
                    )
                else:
                    stranded.status = "failed"
                    stranded.error_code = "interrupted_before_send"
                    stranded.updated_at = clock.now()
                    stranded.version += 1
                    _problem(
                        session,
                        clock,
                        "EMAIL_DELIVERY_FAILED",
                        f"email-delivery:{stranded.id}",
                        {
                            "delivery_id": str(stranded.id),
                            "cleaner_id": str(stranded.cleaner_id),
                            "service_date": stranded.service_date.isoformat(),
                            "reason_code": "interrupted_before_send",
                        },
                    )
                    counts["blocked"] += 1
                    logger.error(
                        "email_automation.stranded_failed delivery_id=%s kind=%s attempt=%s",
                        stranded.id,
                        stranded.kind,
                        stranded.attempt_count,
                    )
            else:
                stranded.status = "uncertain"
                stranded.error_code = "interrupted_submission"
                stranded.updated_at = clock.now()
                stranded.version += 1
                _problem(
                    session,
                    clock,
                    "EMAIL_DELIVERY_UNCERTAIN",
                    f"email-delivery:{stranded.id}",
                    {
                        "delivery_id": str(stranded.id),
                        "cleaner_id": str(stranded.cleaner_id),
                        "service_date": stranded.service_date.isoformat(),
                    },
                )
                counts["uncertain"] += 1
        session.commit()
    for _ in range(20):
        with factory() as session:
            acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
            row = session.scalar(
                select(EmailDelivery)
                .where(
                    EmailDelivery.kind.in_(kinds),
                    EmailDelivery.status == "queued",
                    EmailDelivery.next_attempt_at <= clock.now(),
                    EmailDelivery.attempt_count < 3,
                )
                .order_by(EmailDelivery.next_attempt_at, EmailDelivery.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                break
            row.status = "sending"
            row.claimed_at = row.updated_at = clock.now()
            row.attempt_count += 1
            row.version += 1
            identity = row.id
            claimed_version = row.version
            session.commit()

        send_params: dict[str, str] | None = None
        validation_error: AppError | None = None
        requeue_not_ready = False
        with factory() as session:
            acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
            row = session.scalar(select(EmailDelivery).where(EmailDelivery.id == identity).with_for_update())
            if row is None or row.status != "sending" or row.version != claimed_version:
                continue
            try:
                snapshot = json.loads(reveal_text(row.snapshot_ciphertext, settings.field_encryption_secret))
                reservation_change = snapshot.get("notification") == "reservation_change"
                if row.kind == "assignment" and not reservation_change:
                    previous = session.scalar(
                        select(EmailDelivery.id)
                        .where(
                            EmailDelivery.cleaner_id == row.cleaner_id,
                            EmailDelivery.service_date == row.service_date,
                            EmailDelivery.kind == "assignment",
                            EmailDelivery.id != row.id,
                            EmailDelivery.status.in_(("accepted", "uncertain")),
                        )
                        .limit(1)
                    )
                    local = clock.now().astimezone(NY)
                    if (
                        previous is None
                        and row.service_date > local.date()
                        and (
                            row.service_date != local.date() + timedelta(days=1)
                            or local.strftime("%H:%M") < settings.dispatch_local_time
                        )
                    ):
                        requeue_not_ready = True
                if requeue_not_ready:
                    row.status = "queued"
                    row.attempt_count -= 1
                    row.claimed_at = None
                    row.next_attempt_at = max(clock.now() + timedelta(minutes=1), row.next_attempt_at)
                    session.commit()
                    continue
                workspace_id = get_workspace_id(session)
                if (
                    row.kind != "onboarding"
                    and not reservation_change
                    and settings.workspace_signup_enabled
                    and workspace_id != LEGACY_WORKSPACE_ID
                ):
                    if current_local_baseline(session, settings) is None:
                        raise AppError("local_routes_unavailable", "Refresh saved driving routes before sending.", 409)
                    if not cleaner_ready_for_scheduling(session, row.cleaner_id, row.service_date):
                        raise AppError(
                            "cleaner_onboarding_incomplete", "Cleaner hours and starting routes are required.", 409
                        )
                if reservation_change:
                    change_event = session.get(OutboxEvent, uuid.UUID(str(snapshot["event_id"])))
                    if change_event is None:
                        raise AppError("change_unavailable", "The reservation change is unavailable.", 409)
                    latest = session.scalar(
                        select(OutboxEvent.id)
                        .where(
                            OutboxEvent.topic == "cleaning.reservation_changed",
                            OutboxEvent.aggregate_id == change_event.aggregate_id,
                        )
                        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
                        .limit(1)
                    )
                    if latest != change_event.id:
                        raise AppError(
                            "superseded_reservation_change", "A newer reservation change replaces this notice.", 409
                        )
                    preview = reservation_change_preview(session, settings, clock, uuid.UUID(str(snapshot["event_id"])))
                elif row.kind == "onboarding":
                    preview = onboarding_preview(session, settings, clock, row.cleaner_id, row.onboarding_grant_id)
                elif row.kind == "offer":
                    offer = session.get(CleaningOffer, row.offer_id)
                    if offer is None or offer.status != "open" or offer.expires_at <= clock.now():
                        raise AppError("offer_closed", "The available cleaning response window closed.", 409)
                    preview = offer_preview(session, settings, clock, offer.id, row.cleaner_id)
                else:
                    preview = assignment_preview(session, settings, clock, row.cleaner_id, row.service_date)
                if preview["fingerprint"] != row.fingerprint:
                    raise AppError(
                        "email_preview_changed", "Schedule or cleaner contact changed before submission.", 409
                    )
                if row.kind == "assignment" and not reservation_change:
                    marker = session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.intent_key == f"automatic-cleaner-plan:{row.service_date.isoformat()}"
                        )
                    )
                    schedule_day = session.scalar(
                        select(ScheduleDay).where(ScheduleDay.service_date == row.service_date)
                    )
                    if (
                        marker is None
                        or schedule_day is None
                        or marker.payload.get("schedule_run_id") != str(schedule_day.active_run_id)
                        or marker.payload.get("day_revision") != schedule_day.revision
                    ):
                        raise AppError("schedule_changed", "Scheduling inputs changed before submission.", 409)
                send_params = {
                    "delivery_id": str(row.id),
                    "recipient": str(preview["recipient"]),
                    "subject": str(preview["subject"]),
                    "body": str(preview["body"]),
                }
                row.version += 1
                session.commit()
            except AppError as error:
                validation_error = error
                session.rollback()
            except (KeyError, InvalidTag):
                session.rollback()
                acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
                row = session.scalar(select(EmailDelivery).where(EmailDelivery.id == identity).with_for_update())
                if row is None or row.status != "sending" or row.version != claimed_version:
                    logger.warning("email_automation.snapshot_error_stale delivery_id=%s", identity, exc_info=True)
                    continue
                row.status = "failed"
                row.error_code = "invalid_snapshot"
                row.updated_at = clock.now()
                row.version += 1
                _problem(
                    session,
                    clock,
                    "EMAIL_DELIVERY_FAILED",
                    f"email-delivery:{row.id}",
                    {
                        "delivery_id": str(row.id),
                        "cleaner_id": str(row.cleaner_id),
                        "service_date": row.service_date.isoformat(),
                        "reason_code": "invalid_snapshot",
                    },
                )
                session.commit()
                logger.error("email_automation.invalid_snapshot delivery_id=%s", identity, exc_info=True)
                counts["blocked"] += 1
                continue
            except Exception:
                session.rollback()
                acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
                row = session.scalar(select(EmailDelivery).where(EmailDelivery.id == identity).with_for_update())
                if row is None or row.status != "sending" or row.version != claimed_version:
                    logger.warning("email_automation.pre_send_error_stale delivery_id=%s", identity, exc_info=True)
                    continue
                if row.attempt_count < 3:
                    row.status = "queued"
                    row.next_attempt_at = clock.now() + timedelta(minutes=1)
                    row.error_code = "pre_send_error"
                    row.updated_at = clock.now()
                    row.version += 1
                    session.commit()
                    logger.warning("email_automation.validation_error delivery_id=%s", identity, exc_info=True)
                else:
                    row.status = "failed"
                    row.error_code = "pre_send_error"
                    row.updated_at = clock.now()
                    row.version += 1
                    _problem(
                        session,
                        clock,
                        "EMAIL_DELIVERY_FAILED",
                        f"email-delivery:{row.id}",
                        {
                            "delivery_id": str(row.id),
                            "cleaner_id": str(row.cleaner_id),
                            "service_date": row.service_date.isoformat(),
                            "reason_code": "pre_send_error",
                        },
                    )
                    session.commit()
                    logger.error("email_automation.pre_send_error delivery_id=%s", identity, exc_info=True)
                    counts["blocked"] += 1
                continue

        expected_version = claimed_version + 1 if send_params is not None else claimed_version
        client = None
        submitted = False
        smtp_error: Exception | None = None
        if send_params is not None:
            try:
                client, sender = connect(settings.gmail_credentials_file)
                with factory() as session:
                    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
                    row = session.scalar(select(EmailDelivery).where(EmailDelivery.id == identity).with_for_update())
                    if row is None or row.status != "sending" or row.version != expected_version:
                        client.close()
                        continue
                    row.submitting_at = clock.now()
                    row.version += 1
                    session.commit()
                    expected_version += 1
                submitted = True
                send_connected_email(
                    client,
                    sender,
                    send_params["delivery_id"],
                    send_params["recipient"],
                    send_params["subject"],
                    send_params["body"],
                )
            except (OSError, ValueError, smtplib.SMTPException) as exc:
                smtp_error = exc
            finally:
                if client is not None:
                    client.close()

        with factory() as session:
            acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
            row = session.scalar(select(EmailDelivery).where(EmailDelivery.id == identity).with_for_update())
            if row is None or row.status != "sending" or row.version != expected_version:
                continue
            if validation_error is not None:
                err_code = validation_error.code
                row.status = "queued" if err_code == "stale_source" else "stale"
                row.error_code = err_code
                if row.kind == "offer" and err_code in {"offer_closed", "offer_unavailable", "offer_ineligible"}:
                    row.status = "stale"
                if row.status == "queued":
                    row.attempt_count -= 1
                    row.next_attempt_at = clock.now() + timedelta(minutes=1)
                elif err_code != "superseded_reservation_change" and not (
                    row.kind == "offer" and err_code in {"offer_closed", "offer_unavailable", "offer_ineligible"}
                ):
                    _problem(
                        session,
                        clock,
                        "ASSIGNMENT_EMAIL_BLOCKED",
                        f"email-delivery:{row.id}",
                        {
                            "delivery_id": str(row.id),
                            "cleaner_id": str(row.cleaner_id),
                            "service_date": row.service_date.isoformat(),
                            "reason_code": err_code,
                        },
                    )
                counts["blocked"] += 1
            elif smtp_error is not None:
                row.status = "uncertain" if submitted else "queued" if row.attempt_count < 3 else "failed"
                row.error_code = "submission_uncertain" if submitted else "gmail_connection_failed"
                row.next_attempt_at = clock.now() + timedelta(minutes=2**row.attempt_count)
                if row.status in {"uncertain", "failed"}:
                    _problem(
                        session,
                        clock,
                        "EMAIL_DELIVERY_UNCERTAIN" if submitted else "EMAIL_DELIVERY_FAILED",
                        f"email-delivery:{row.id}",
                        {
                            "delivery_id": str(row.id),
                            "cleaner_id": str(row.cleaner_id),
                            "service_date": row.service_date.isoformat(),
                        },
                    )
                counts["uncertain" if submitted else "blocked"] += 1
            else:
                row.status = "accepted"
                row.accepted_at = clock.now()
                row.error_code = None
                counts["accepted"] += 1
            row.updated_at = clock.now()
            row.version += 1
            write_audit(
                session,
                clock=clock,
                actor_type="system",
                actor_subject=_ACTOR,
                action="email.delivery_attempt",
                entity_type="email_delivery",
                entity_id=row.id,
                correlation_id=row.id,
                reason=None,
                redacted_changes={
                    "status": row.status,
                    "attempt_count": row.attempt_count,
                    "error_code": row.error_code,
                },
                request_id=None,
            )
            session.commit()
            logger.info(
                "email_automation_delivery delivery_id=%s status=%s attempts=%s", row.id, row.status, row.attempt_count
            )
    return counts


def run_email_automation(factory: sessionmaker[Session], settings: Settings, clock: Clock) -> dict[str, object]:
    """Run the email automation cycle.

    Lock strategy: Advisory locks are NOT held during solver execution.
    1. Phase 1 (under lock): Check which days need planning, gather preserved_ids
    2. Phase 2 (no lock): Run solver for each day that needs it
    3. Phase 3 (under lock): Finalize planning markers, queue emails, commit
    """
    if not settings.local_email_automation_enabled:
        return {"status": "disabled"}
    if (
        not settings.hospitable_read_only
        or not settings.local_scheduling_enabled
        or not settings.gmail_assignment_send_enabled
    ):
        raise AppError(
            "email_automation_isolation_required", "Automatic email requires isolated local scheduling.", 403
        )
    now = clock.now().astimezone(NY)
    result: dict[str, object] = {"status": "ready", "planned": 0, "queued": 0}

    days_to_plan: list[tuple[date, tuple[uuid.UUID, ...], bool]] = []
    days_already_ready: list[date] = []

    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        if publication_blocked(session, clock=clock, settings=settings):
            return {"status": "waiting_for_source", "planned": 0, "queued": 0}
        workspace_id = get_workspace_id(session)
        immediate = settings.workspace_signup_enabled and workspace_id != LEGACY_WORKSPACE_ID
        routes_ready = True
        if immediate:
            if current_local_baseline(session, settings) is None:
                _problem(
                    session,
                    clock,
                    "AUTOMATIC_SCHEDULE_BLOCKED",
                    "automatic-schedule:routes",
                    {"reason_code": "local_routes_unavailable"},
                )
                routes_ready = False
                result["status"] = "waiting_for_routes"
            else:
                _resolve(session, clock, "automatic-schedule:routes")
        planning_day = (
            now.date() + timedelta(days=1)
            if immediate or now.strftime("%H:%M") >= settings.dispatch_local_time
            else now.date()
        )
        days = {now.date(), planning_day}
        for event in session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.topic == "cleaning.reservation_changed", OutboxEvent.processed_at.is_(None)
            )
        ):
            old_day = date.fromisoformat(str(event.payload["service_date"]))
            if old_day >= now.date():
                days.add(old_day)
            task = session.get(CleaningTask, uuid.UUID(str(event.payload["task_id"])))
            if task is not None and task.status in {"ready", "assigned", "pending_deadline"}:
                new_day = task.release_at.astimezone(NY).date()
                if new_day >= now.date():
                    days.add(new_day)
        for day in sorted(days) if routes_ready else []:
            previous_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == day))
            was_published = previous_day is not None and previous_day.active_run_id is not None
            check = _check_planning_needed(session, clock, day)
            if check.needs_planning:
                days_to_plan.append((day, check.preserved_ids, was_published))
            elif check.skip_reason is None:
                days_already_ready.append(day)
        session.commit()

    solver_results: dict[date, ScheduleRun] = {}
    solver_errors: dict[date, AppError] = {}
    for day, preserved_ids, _was_published in days_to_plan:
        try:
            run = _run_solver_and_publish(factory, settings, clock, day, preserved_ids)
            solver_results[day] = run
        except AppError as error:
            solver_errors[day] = error

    ready_days: list[date] = []
    planned = 0
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        for day, _preserved_ids, was_published in days_to_plan:
            if day in solver_errors:
                _problem(
                    session,
                    clock,
                    "AUTOMATIC_SCHEDULE_BLOCKED",
                    f"automatic-schedule:{day.isoformat()}",
                    {"service_date": day.isoformat(), "reason_code": solver_errors[day].code},
                )
                result["status"] = "planning_blocked"
                continue
            solved_run = solver_results.get(day)
            try:
                if _finalize_planning(session, clock, day, solved_run):
                    ready_days.append(day)
                    if not was_published:
                        planned += 1
                    _resolve(session, clock, f"automatic-schedule:{day.isoformat()}")
            except AppError as error:
                _problem(
                    session,
                    clock,
                    "AUTOMATIC_SCHEDULE_BLOCKED",
                    f"automatic-schedule:{day.isoformat()}",
                    {"service_date": day.isoformat(), "reason_code": error.code},
                )
                result["status"] = "planning_blocked"
        for day in days_already_ready:
            if _finalize_planning(session, clock, day, None):
                ready_days.append(day)
                _resolve(session, clock, f"automatic-schedule:{day.isoformat()}")
        queued = 0
        for day in ready_days:
            queued += _queue(session, settings, clock, day)
        queued += _queue_reservation_changes(session, settings, clock)
        queued += _queue_offers(session, settings, clock)
        result["planned"] = planned
        result["queued"] = queued
        session.commit()
    result.update(_drain(factory, settings, clock))
    logger.info(
        "email_automation_tick planned=%s queued=%s accepted=%s blocked=%s",
        result["planned"],
        result["queued"],
        result["accepted"],
        result["blocked"],
    )
    return result
