from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.errors import PermanentFailure, ProviderUnavailable
from app.adapters.sms.mock import RecordingSmsGateway
from app.adapters.types import DeliveryUpdate, VerifiedSmsEvent
from app.clock import Clock
from app.config import AppEnv, Settings
from app.domain.errors import AppError
from app.domain.sms_commands import SmsCommand, parse_sms_command
from app.domain.tasks import effective_deadline
from app.logging_redaction import redact_text
from app.models import (
    Acknowledgment,
    Assignment,
    Cleaner,
    CleanerAccessLink,
    CleanerScheduleVersion,
    CleaningTask,
    OperationalException,
    OutboxEvent,
    PolicyVersion,
    Property,
    SmsMessage,
)
from app.services.audit import write_audit
from app.services.field_cipher import protect_text, reveal_text
from app.services.instructions import effective_public, latest_instruction
from app.services.schedules import mark_unavailable
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id

NY = ZoneInfo("America/New_York")

_LIVE = ("planned", "published", "acknowledged", "blocked", "completed")
_TERMINAL_SUCCESS = {"delivered"}
_TERMINAL_FAILURE = {"undeliverable", "failed"}
_RANK = {
    "canceled": 0,
    "queued": 1,
    "accepted": 2,
    "unknown": 2,
    "sent": 3,
    "delivered": 4,
    "undeliverable": 5,
    "failed": 5,
}


def recording_block_reason(settings: Settings, *, policy_approved: bool) -> str | None:
    """Mock recording is local only. Live flags and real providers stay closed."""
    if settings.app_env not in {AppEnv.test, AppEnv.development}:
        return "environment_not_isolated"
    providers = (settings.sms_provider, settings.reservation_provider, settings.routing_provider)
    if providers != ("mock", "mock", "mock"):
        return "provider_not_mock"
    if settings.production_dispatch_enabled or settings.production_integrations_enabled:
        return "flags_fail_closed"
    if any(
        (
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_messaging_service_sid,
            settings.google_maps_api_key,
            settings.hospitable_credential_secret_ref,
        )
    ):
        return "credentials_present"
    if not policy_approved:
        return "policy_not_approved"
    return None


def enqueue_schedule_messages(
    session: Session, *, clock: Clock, settings: Settings, service_date: date | None = None
) -> dict[str, object]:
    blocked = recording_block_reason(settings, policy_approved=_approved(session))
    if blocked is not None:
        return {"queued": 0, "blocked": blocked, "external_send": False}
    queued = 0
    versions = session.scalars(select(CleanerScheduleVersion)).all()
    for version in versions:
        if service_date is not None and version.service_date != service_date:
            continue
        if version.status == "current":
            queued += _queue_current(session, clock, settings, version)
        elif _was_sent(session, version):
            queued += _queue_cancellation(session, clock, settings, version)
    session.flush()
    return {"queued": queued, "blocked": None, "external_send": False}


def drain_mock_sms(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    blocked = recording_block_reason(settings, policy_approved=_approved(session))
    gateway = RecordingSmsGateway()
    if blocked is not None:
        return {"recorded": 0, "blocked": blocked, "gateway_calls": 0, "external_send": False}
    recorded = 0
    rows = session.scalars(
        select(SmsMessage).where(SmsMessage.direction == "outbound", SmsMessage.status == "queued").with_for_update()
    ).all()
    for row in rows:
        if not _still_sendable(session, row):
            _mark(row, "canceled", clock.now(), error=None)
            continue
        cleaner = session.get(Cleaner, row.cleaner_id) if row.cleaner_id else None
        if cleaner is not None:
            session.refresh(cleaner)
        if cleaner is not None and cleaner.sms_consent_status != "opted_in":
            _mark(row, "canceled", clock.now(), error="opted_out")
            continue
        body = reveal_text(row.body_encrypted or b"", settings.field_encryption_secret)
        try:
            result = gateway.send(str(row.id), "synthetic", body, "https://example.invalid/mock-status")
        except PermanentFailure:
            row.attempt_count += 1
            _mark(row, "failed", clock.now(), error="undeliverable")
            _exception(session, clock, "SMS_DELIVERY", f"sms-delivery:{row.id}", row.cleaner_id, "permanent")
            continue
        except ProviderUnavailable:
            row.attempt_count += 1
            row.updated_at = clock.now()
            if row.attempt_count >= 3:
                _mark(row, "failed", clock.now(), error="retry_exhausted")
                _exception(session, clock, "SMS_DELIVERY", f"sms-delivery:{row.id}", row.cleaner_id, "retries")
            continue
        row.attempt_count += 1
        row.provider_message_id = result.provider_message_id
        row.provider_status_at = clock.now()
        if result.ambiguous or not result.accepted:
            _mark(row, "unknown", clock.now(), error="ambiguous")
            _exception(session, clock, "SMS_AMBIGUOUS", f"sms-ambiguous:{row.id}", row.cleaner_id, "ambiguous")
        else:
            _mark(row, "accepted", clock.now(), error=None)
            row.accepted_at = clock.now()
        recorded += 1
    session.flush()
    return {
        "recorded": recorded,
        "blocked": None,
        "gateway_calls": len(gateway.sent),
        "external_send": False,
    }


def run_ack_timers(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    policy = _policy(session)
    if policy is None:
        return {"reminders": 0, "escalations": 0, "reassigned": False}
    reminder_minutes = policy.settings.get("ack_reminder_minutes")
    escalation_minutes = policy.settings.get("ack_escalation_minutes")
    if not isinstance(reminder_minutes, int) or not isinstance(escalation_minutes, int):
        return {"reminders": 0, "escalations": 0, "reassigned": False}
    reminders = 0
    escalations = 0
    messages = session.scalars(
        select(SmsMessage).where(
            SmsMessage.purpose == "assignment",
            SmsMessage.status.in_(("accepted", "sent", "delivered")),
            SmsMessage.part_number == 1,
        )
    ).all()
    for message in messages:
        if message.accepted_at is None or message.cleaner_id is None or message.service_date is None:
            continue
        version = _version_row(session, message)
        if version is None or version.status != "current":
            continue
        if _ack_for(session, version.id) is not None:
            continue
        cleaner = session.get(Cleaner, message.cleaner_id)
        if cleaner is None or cleaner.sms_consent_status != "opted_in":
            continue
        if clock.now() >= message.accepted_at + timedelta(minutes=reminder_minutes):
            created = _queue_text(
                session,
                clock,
                settings,
                cleaner_id=message.cleaner_id,
                version=version,
                purpose="reminder",
                part=1,
                parts=1,
                body=(
                    f"Acceptance still needed for {message.service_date.isoformat()} "
                    f"V{message.schedule_version_number}. Reply CONFIRM {message.service_date.isoformat()} "
                    f"V{message.schedule_version_number}, or contact manager. Delivery is not acceptance."
                ),
            )
            reminders += int(created)
        if clock.now() >= message.accepted_at + timedelta(minutes=escalation_minutes):
            opened = _exception(
                session,
                clock,
                "MISSING_ACK",
                f"sms-missing-ack:{message.cleaner_id}:{message.service_date}:{message.schedule_version_number}",
                message.cleaner_id,
                "missing acknowledgment",
            )
            if opened:
                escalations += 1
                _operator_alert(session, clock, message)
    session.flush()
    return {"reminders": reminders, "escalations": escalations, "reassigned": False}


def receive_inbound(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    event: VerifiedSmsEvent,
) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    command = parse_sms_command(event.body)
    cleaner = _cleaner_for_sender(session, settings, event.sender)
    _store_inbound(session, clock, settings, event, cleaner)
    if cleaner is None:
        if command.kind == "help":
            _queue_anonymous_help(session, clock, settings, event.message_id)
        drain_mock_sms(session, clock=clock, settings=settings)
        return {"applied": False, "reason": "unknown_sender", "external_send": False}
    if command.kind == "stop":
        _stop(session, clock, cleaner)
    elif command.kind == "help":
        _queue_help(session, clock, settings, cleaner, event.message_id)
    elif command.kind == "confirm":
        _confirm(session, clock, settings, cleaner, command, event.message_id)
    elif command.kind == "done":
        _done(session, clock, settings, cleaner, command, event.message_id)
    elif command.kind == "unavailable":
        _unavailable(session, clock, settings, cleaner, command, event.message_id)
    else:
        _queue_reject(session, clock, settings, cleaner, event.message_id, command.reason or "rejected")
    write_audit(
        session,
        clock=clock,
        actor_type="system",
        actor_subject=str(cleaner.id),
        action="sms.command",
        entity_type="cleaner",
        entity_id=cleaner.id,
        correlation_id=uuid.uuid4(),
        reason=command.kind,
        redacted_changes={"command": command.kind, "reason": command.reason},
        request_id=None,
    )
    drain_mock_sms(session, clock=clock, settings=settings)
    return {"applied": True, "command": command.kind, "external_send": False}


def apply_status(session: Session, *, clock: Clock, update: DeliveryUpdate) -> dict[str, object]:
    row = session.scalar(select(SmsMessage).where(SmsMessage.provider_message_id == update.provider_message_id))
    if row is None:
        _exception(session, clock, "SMS_UNKNOWN_CALLBACK", f"sms-unknown:{update.provider_message_id}", None, "unknown")
        session.flush()
        return {"updated": False, "quarantined": True}
    new_status = update.status if update.status in _RANK else "unknown"
    if row.status in _TERMINAL_SUCCESS and new_status != row.status:
        _exception(
            session,
            clock,
            "SMS_STATUS_CONFLICT",
            f"sms-conflict:{row.id}:{new_status}",
            row.cleaner_id,
            "terminal",
        )
        session.flush()
        return {"updated": False, "status": row.status}
    if row.status in _TERMINAL_FAILURE and new_status != row.status:
        _exception(
            session,
            clock,
            "SMS_STATUS_CONFLICT",
            f"sms-conflict:{row.id}:{new_status}",
            row.cleaner_id,
            "terminal",
        )
        session.flush()
        return {"updated": False, "status": row.status}
    if _RANK[new_status] < _RANK.get(row.status, 0):
        session.flush()
        return {"updated": False, "status": row.status}
    if new_status == row.status:
        session.flush()
        return {"updated": False, "status": row.status}
    _mark(row, new_status, clock.now(), error=update.error_code)
    if new_status in _TERMINAL_FAILURE:
        _exception(session, clock, "SMS_DELIVERY", f"sms-delivery:{row.id}", row.cleaner_id, new_status)
    session.flush()
    return {"updated": True, "status": row.status}


def set_consent(
    session: Session,
    *,
    clock: Clock,
    cleaner_id: uuid.UUID,
    status: str,
    evidence_ref: str,
    actor: str,
) -> Cleaner:
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None:
        raise AppError("not_found", "Cleaner was not found.", 404)
    if status not in {"opted_in", "opted_out"}:
        raise AppError("validation_error", "Consent status is invalid.", 422)
    if cleaner.sms_consent_status == "opted_out" and status == "opted_in":
        if not evidence_ref.startswith("synthetic-fresh-consent:"):
            raise AppError("consent_required", "Opt-out stays until fresh consent evidence is recorded.", 409)
    cleaner.sms_consent_status = status
    cleaner.consent_evidence_ref = evidence_ref
    cleaner.opted_out_at = clock.now() if status == "opted_out" else None
    cleaner.version += 1
    cleaner.updated_at = clock.now()
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="cleaner.consent",
        entity_type="cleaner",
        entity_id=cleaner.id,
        correlation_id=cleaner.id,
        reason=status,
        redacted_changes={"sms_consent_status": status},
        request_id=None,
    )
    session.flush()
    return cleaner


def issue_link(session: Session, *, clock: Clock, settings: Settings, cleaner_id: uuid.UUID, ttl: timedelta) -> str:
    if recording_block_reason(settings, policy_approved=_approved(session)) is not None:
        raise AppError("dispatch_disabled", "Cleaner links stay unavailable outside isolated mock mode.", 403)
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("not_found", "Active cleaner was not found.", 404)
    token = uuid.uuid4().hex + uuid.uuid4().hex
    session.add(
        CleanerAccessLink(
            id=uuid.uuid4(),
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + ttl,
            revoked_at=None,
        )
    )
    session.flush()
    return token


def read_link(session: Session, *, clock: Clock, token: str) -> dict[str, object]:
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = session.scalar(
        select(CleanerAccessLink).where(CleanerAccessLink.token_hash == digest, CleanerAccessLink.purpose == "jobs")
    )
    if row is None or row.revoked_at is not None or row.expires_at <= clock.now():
        raise AppError("not_found", "Cleaner link is not available.", 404)
    cleaner = session.get(Cleaner, row.cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("not_found", "Cleaner link is not available.", 404)
    assignments = session.scalars(
        select(Assignment).where(
            Assignment.cleaner_id == row.cleaner_id,
            Assignment.status.in_(("published", "acknowledged")),
        )
    ).all()
    jobs: list[dict[str, object]] = []
    for assignment in assignments:
        task = session.get(CleaningTask, assignment.task_id)
        prop = None if task is None else session.get(Property, task.property_id)
        instruction = None if task is None else latest_instruction(session, task.property_id)
        public = {} if instruction is None else effective_public(instruction)
        safe_public = {key: value for key, value in public.items() if "code" not in key}
        jobs.append(
            {
                "task_id": None if task is None else str(task.id),
                "property_alias": None if prop is None else prop.external_id,
                "start": assignment.planned_start.isoformat(),
                "end": assignment.planned_end.isoformat(),
                "public_notes": safe_public,
            }
        )
    return {"cleaner_id": str(row.cleaner_id), "jobs": jobs}


def preview_text(session: Session, settings: Settings, message: SmsMessage) -> str:
    if not message.body_encrypted:
        return ""
    revealed = reveal_text(message.body_encrypted, settings.field_encryption_secret)
    return redact_text(revealed)[:80]


def _queue_current(session: Session, clock: Clock, settings: Settings, version: CleanerScheduleVersion) -> int:
    assignments = session.scalars(
        select(Assignment).where(
            Assignment.cleaner_schedule_version_id == version.id,
            Assignment.status.in_(("published", "acknowledged")),
        )
    ).all()
    lines: list[str] = []
    for assignment in sorted(assignments, key=lambda item: item.planned_start):
        task = session.get(CleaningTask, assignment.task_id)
        prop = None if task is None else session.get(Property, task.property_id)
        alias = "property" if prop is None else prop.name
        if prop is not None and prop.provider == "hospitable":
            if not prop.location_ciphertext:
                raise AppError(
                    "missing_destination", "A scheduled house needs a verified destination before messaging.", 422
                )
            location = json.loads(reveal_text(prop.location_ciphertext, settings.field_encryption_secret))
            address, unit = location.get("display_address"), location.get("unit")
            if not address:
                raise AppError(
                    "missing_destination", "A scheduled house needs a verified destination before messaging.", 422
                )
            alias = f"{alias}, {address}" + (f", unit {unit}" if unit else "")
        start = assignment.planned_start.astimezone(NY).strftime("%H:%M")
        end = assignment.planned_end.astimezone(NY).strftime("%H:%M")
        task_id = "task" if task is None else str(task.id)
        deadline = ""
        if task is not None:
            due = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at).astimezone(NY)
            deadline = f"; ready by {due.strftime('%b %d %H:%M')} Eastern"
        lines.append(f"{task_id} {alias} {start}-{end} Eastern{deadline}. Reply DONE {task_id} when cleaned")
    if not lines:
        return 0
    created = 0
    total = len(lines)
    for index, line in enumerate(lines, start=1):
        body = (
            f"{version.service_date.isoformat()} schedule V{version.version_number} part {index}/{total}: "
            f"{line}. Reply CONFIRM {version.service_date.isoformat()} V{version.version_number}."
        )
        created += int(
            _queue_text(
                session,
                clock,
                settings,
                cleaner_id=version.cleaner_id,
                version=version,
                purpose="assignment",
                part=index,
                parts=total,
                body=body,
            )
        )
    return created


def _queue_cancellation(session: Session, clock: Clock, settings: Settings, version: CleanerScheduleVersion) -> int:
    prior = session.scalars(select(Assignment).where(Assignment.cleaner_schedule_version_id == version.id)).all()
    ids = ",".join(str(row.task_id) for row in prior) or "none"
    body = (
        f"{version.service_date.isoformat()} V{version.version_number} is no longer active. "
        f"Do not use its job instructions ({ids}). Follow only your latest schedule. "
        "Contact manager if work already started."
    )
    return int(
        _queue_text(
            session,
            clock,
            settings,
            cleaner_id=version.cleaner_id,
            version=version,
            purpose="cancellation",
            part=1,
            parts=1,
            body=body,
        )
    )


def _queue_text(
    session: Session,
    clock: Clock,
    settings: Settings,
    *,
    cleaner_id: uuid.UUID | None,
    version: CleanerScheduleVersion | None,
    purpose: str,
    part: int,
    parts: int,
    body: str,
) -> bool:
    service_date = None if version is None else version.service_date
    version_number = None if version is None else version.version_number
    identity = "none" if cleaner_id is None else str(cleaner_id)
    date_key = "none" if service_date is None else service_date.isoformat()
    version_key = "none" if version_number is None else str(version_number)
    intent = f"sms:{purpose}:{identity}:{date_key}:{version_key}:{part}"
    existing = session.scalar(select(SmsMessage).where(SmsMessage.intent_key == intent))
    if existing is not None:
        return False
    encrypted, digest = _protect(body, settings.field_encryption_secret)
    now = clock.now()
    session.add(
        SmsMessage(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=cleaner_id,
            cleaner_schedule_version_id=None if version is None else version.id,
            direction="outbound",
            purpose=purpose,
            intent_key=intent,
            provider="mock",
            provider_message_id=None,
            body_encrypted=encrypted,
            body_digest=digest,
            status="queued",
            status_rank=_RANK["queued"],
            provider_status_at=None,
            attempt_count=0,
            accepted_at=None,
            error_code=None,
            correlation_id=uuid.uuid4(),
            expires_at=now + timedelta(hours=24),
            service_date=service_date,
            schedule_version_number=version_number,
            part_number=part,
            part_count=parts,
        )
    )
    session.flush()
    return True


def _store_inbound(
    session: Session,
    clock: Clock,
    settings: Settings,
    event: VerifiedSmsEvent,
    cleaner: Cleaner | None,
) -> None:
    encrypted, digest = _protect(event.body, settings.field_encryption_secret)
    now = clock.now()
    session.add(
        SmsMessage(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=None if cleaner is None else cleaner.id,
            cleaner_schedule_version_id=None,
            direction="inbound",
            purpose="inbound",
            intent_key=f"sms:inbound:{event.message_id}",
            provider="mock",
            provider_message_id=event.message_id,
            body_encrypted=encrypted,
            body_digest=digest,
            status="accepted",
            status_rank=_RANK["accepted"],
            provider_status_at=now,
            attempt_count=0,
            accepted_at=now,
            error_code=None,
            correlation_id=uuid.uuid4(),
            expires_at=None,
            service_date=None,
            schedule_version_number=None,
            part_number=None,
            part_count=None,
        )
    )
    session.flush()


def _confirm(
    session: Session,
    clock: Clock,
    settings: Settings,
    cleaner: Cleaner,
    command: SmsCommand,
    message_id: str,
) -> None:
    current = _current_versions(session, cleaner.id)
    target: CleanerScheduleVersion | None
    if command.service_date is None or command.version is None:
        pending = [version for version in current if _ack_for(session, version.id) is None]
        if len(pending) != 1:
            _queue_reject(session, clock, settings, cleaner, message_id, "ambiguous_confirm")
            return
        target = pending[0]
    else:
        target = next(
            (
                version
                for version in current
                if version.service_date == command.service_date and version.version_number == command.version
            ),
            None,
        )
        if target is None:
            hint = ""
            if len(current) == 1:
                hint = f" Use CONFIRM {current[0].service_date.isoformat()} V{current[0].version_number}."
            _queue_reject(session, clock, settings, cleaner, message_id, "stale_version", extra=hint)
            return
    existing = _ack_for(session, target.id)
    if existing is not None:
        return
    now = clock.now()
    session.add(
        Acknowledgment(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            cleaner_id=cleaner.id,
            cleaner_schedule_version_id=target.id,
            sms_message_id=None,
            actor_subject=str(cleaner.id),
            accepted_at=now,
            method="sms",
        )
    )
    for assignment in session.scalars(
        select(Assignment).where(
            Assignment.cleaner_schedule_version_id == target.id,
            Assignment.status.in_(("published", "planned")),
        )
    ).all():
        assignment.status = "acknowledged"
        assignment.acknowledged_at = now
        assignment.version += 1
        assignment.updated_at = now
    session.flush()


def _done(
    session: Session,
    clock: Clock,
    settings: Settings,
    cleaner: Cleaner,
    command: SmsCommand,
    message_id: str,
) -> None:
    task = None if command.task_token is None else _task_for_token(session, command.task_token)
    if task is None:
        _queue_reject(session, clock, settings, cleaner, message_id, "unknown_task")
        return
    live = session.scalar(
        select(Assignment).where(Assignment.task_id == task.id, Assignment.status.in_(_LIVE)).limit(1)
    )
    own_history = session.scalar(
        select(Assignment).where(Assignment.task_id == task.id, Assignment.cleaner_id == cleaner.id).limit(1)
    )
    if live is None or live.cleaner_id != cleaner.id:
        if own_history is not None and own_history.status in {"superseded", "canceled", "blocked"}:
            _exception(
                session,
                clock,
                "STALE_COMPLETION",
                f"sms-stale-done:{task.id}:{cleaner.id}",
                cleaner.id,
                "stale",
            )
            _queue_reject(session, clock, settings, cleaner, message_id, "stale_completion")
            return
        _queue_reject(session, clock, settings, cleaner, message_id, "wrong_cleaner")
        return
    if task.status == "completed" and task.completion_actor == f"cleaner:{cleaner.id}":
        return
    if _ack_for(session, live.cleaner_schedule_version_id) is None:
        _exception(session, clock, "COMPLETED_WITHOUT_ACK", f"sms-done-no-ack:{task.id}", cleaner.id, "no ack")
    now = clock.now()
    live.status = "completed"
    live.completed_at = now
    live.version += 1
    live.updated_at = now
    task.status = "completed"
    task.completed_at = now
    task.completion_actor = f"cleaner:{cleaner.id}"
    task.version += 1
    task.updated_at = now
    _queue_text(
        session,
        clock,
        settings,
        cleaner_id=cleaner.id,
        version=session.get(CleanerScheduleVersion, live.cleaner_schedule_version_id),
        purpose="completion",
        part=1,
        parts=1,
        body=f"Recorded DONE {task.id} for your current assignment. Thank you.",
    )
    session.flush()


def _unavailable(
    session: Session,
    clock: Clock,
    settings: Settings,
    cleaner: Cleaner,
    command: SmsCommand,
    message_id: str,
) -> None:
    current = _current_versions(session, cleaner.id)
    service_date = command.service_date
    if service_date is None:
        dates = {version.service_date for version in current}
        if len(dates) != 1:
            _queue_reject(session, clock, settings, cleaner, message_id, "ambiguous_date")
            return
        service_date = next(iter(dates))
    from app.services.weekly_availability import effective_availability

    availability = next(
        (row for row in effective_availability(session, service_date) if row.cleaner_id == cleaner.id), None
    )
    if availability is None:
        _queue_reject(session, clock, settings, cleaner, message_id, "availability_missing")
        return
    mark_unavailable(
        session,
        clock=clock,
        settings=settings,
        cleaner_id=cleaner.id,
        service_date=service_date,
        expected_version=availability.version,
        actor=str(cleaner.id),
    )
    enqueue_schedule_messages(session, clock=clock, settings=settings)
    _queue_text(
        session,
        clock,
        settings,
        cleaner_id=cleaner.id,
        version=None,
        purpose="unavailable_receipt",
        part=1,
        parts=1,
        body=(
            f"Marked unavailable for {service_date.isoformat()}. "
            "We are reviewing affected work. Contact manager if a job has started."
        ),
    )


def _stop(session: Session, clock: Clock, cleaner: Cleaner) -> None:
    now = clock.now()
    if cleaner.sms_consent_status != "opted_out":
        cleaner.sms_consent_status = "opted_out"
        cleaner.opted_out_at = now
        cleaner.version += 1
        cleaner.updated_at = now
    for row in session.scalars(
        select(SmsMessage).where(
            SmsMessage.cleaner_id == cleaner.id,
            SmsMessage.direction == "outbound",
            SmsMessage.status == "queued",
        )
    ).all():
        _mark(row, "canceled", now, error="opted_out")
    _exception(session, clock, "CONTACT_OPT_OUT", f"sms-stop:{cleaner.id}", cleaner.id, "opt-out")
    session.flush()


def _current_versions(session: Session, cleaner_id: uuid.UUID) -> list[CleanerScheduleVersion]:
    return list(
        session.scalars(
            select(CleanerScheduleVersion).where(
                CleanerScheduleVersion.cleaner_id == cleaner_id,
                CleanerScheduleVersion.status == "current",
            )
        ).all()
    )


def _ack_for(session: Session, version_id: uuid.UUID) -> Acknowledgment | None:
    return session.scalar(select(Acknowledgment).where(Acknowledgment.cleaner_schedule_version_id == version_id))


def _task_for_token(session: Session, token: str) -> CleaningTask | None:
    try:
        parsed = uuid.UUID(token)
    except ValueError:
        parsed = None
    if parsed is not None:
        return session.get(CleaningTask, parsed)
    compact = token.lower().replace("-", "")
    if len(compact) < 8:
        return None
    matches = [
        row
        for row in session.scalars(select(CleaningTask)).all()
        if str(row.id).replace("-", "").lower().startswith(compact)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _cleaner_for_sender(session: Session, settings: Settings, sender: str) -> Cleaner | None:
    from app.services.phone_numbers import normalize_phone

    try:
        sender = normalize_phone(sender)
    except AppError:
        return None
    digest = hmac.new(settings.field_encryption_secret.encode(), sender.encode(), hashlib.sha256).hexdigest()
    return session.scalar(select(Cleaner).where(Cleaner.phone_lookup_hmac == digest, Cleaner.active.is_(True)))


def _still_sendable(session: Session, row: SmsMessage) -> bool:
    cleaner = session.get(Cleaner, row.cleaner_id) if row.cleaner_id else None
    if cleaner is not None and not cleaner.active:
        return False
    if row.purpose in {"cancellation", "reply", "help", "completion", "unavailable_receipt"}:
        return True
    if row.cleaner_schedule_version_id is None:
        return True
    version = session.get(CleanerScheduleVersion, row.cleaner_schedule_version_id)
    return version is not None and version.status == "current"


def _was_sent(session: Session, version: CleanerScheduleVersion) -> bool:
    return (
        session.scalar(
            select(SmsMessage.id).where(
                SmsMessage.cleaner_schedule_version_id == version.id,
                SmsMessage.purpose == "assignment",
                SmsMessage.status.in_(("accepted", "sent", "delivered", "unknown")),
            )
        )
        is not None
    )


def _version_row(session: Session, message: SmsMessage) -> CleanerScheduleVersion | None:
    if message.cleaner_schedule_version_id is None:
        return None
    return session.get(CleanerScheduleVersion, message.cleaner_schedule_version_id)


def _queue_help(session: Session, clock: Clock, settings: Settings, cleaner: Cleaner, message_id: str) -> None:
    _queue_text(
        session,
        clock,
        settings,
        cleaner_id=cleaner.id,
        version=None,
        purpose="help",
        part=1,
        parts=1,
        body=(
            "Commands: CONFIRM {date} V{version}; DONE {task_id}; UNAVAILABLE [YYYY-MM-DD]; HELP; STOP. "
            "For urgent issues contact your configured manager."
        ),
    )
    _retarget_intent(session, f"sms:help:{cleaner.id}:none:none:1", f"sms:reply:{message_id}")


def _queue_anonymous_help(session: Session, clock: Clock, settings: Settings, message_id: str) -> None:
    _queue_text(
        session,
        clock,
        settings,
        cleaner_id=None,
        version=None,
        purpose="help",
        part=1,
        parts=1,
        body="Commands: HELP; STOP. For urgent issues contact your configured manager.",
    )


def _queue_reject(
    session: Session,
    clock: Clock,
    settings: Settings,
    cleaner: Cleaner,
    message_id: str,
    reason: str,
    extra: str = "",
) -> None:
    _queue_text(
        session,
        clock,
        settings,
        cleaner_id=cleaner.id,
        version=None,
        purpose="reply",
        part=1,
        parts=1,
        body=f"No change made: {reason}.{extra} Contact manager.",
    )
    _retarget_intent(session, f"sms:reply:{cleaner.id}:none:none:1", f"sms:reply:{message_id}")


def _retarget_intent(session: Session, old: str, new: str) -> None:
    row = session.scalar(select(SmsMessage).where(SmsMessage.intent_key == old))
    if row is not None and session.scalar(select(SmsMessage).where(SmsMessage.intent_key == new)) is None:
        row.intent_key = new


def _mark(row: SmsMessage, status: str, now: datetime, error: str | None) -> None:
    row.status = status
    row.status_rank = _RANK[status]
    row.error_code = error
    row.updated_at = now
    row.version += 1


def _protect(body: str, secret: str) -> tuple[bytes, str]:
    return protect_text(body, secret), hashlib.sha256(body.encode()).hexdigest()


def _approved(session: Session) -> bool:
    return _policy(session) is not None


def _policy(session: Session) -> PolicyVersion | None:
    return session.scalar(
        select(PolicyVersion).where(PolicyVersion.is_active.is_(True), PolicyVersion.approved_at.is_not(None)).limit(1)
    )


def _exception(
    session: Session,
    clock: Clock,
    code: str,
    dedupe: str,
    cleaner_id: uuid.UUID | None,
    detail: str,
) -> bool:
    del cleaner_id
    existing = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == dedupe,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if existing is not None:
        return False
    now = clock.now()
    session.add(
        OperationalException(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            code=code,
            severity="warning",
            dedupe_key=dedupe,
            status="open",
            task_id=None,
            assignment_id=None,
            sync_run_id=None,
            owner_subject=None,
            details={"reason": detail},
            opened_at=now,
            resolved_at=None,
            resolution_reason=None,
        )
    )
    return True


def _operator_alert(session: Session, clock: Clock, message: SmsMessage) -> None:
    key = f"operator.missing_ack:{message.cleaner_id}:{message.service_date}:{message.schedule_version_number}"
    if session.scalar(select(OutboxEvent).where(OutboxEvent.intent_key == key)) is not None:
        return
    now = clock.now()
    session.add(
        OutboxEvent(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            topic="operator.missing_ack",
            aggregate_id=message.id,
            aggregate_version=1,
            intent_key=key,
            payload={
                "cleaner_id": None if message.cleaner_id is None else str(message.cleaner_id),
                "service_date": None if message.service_date is None else message.service_date.isoformat(),
                "version": message.schedule_version_number,
                "sms_sent": False,
            },
            available_at=now,
            claimed_until=None,
            processed_at=None,
            attempt_count=0,
            last_error_code=None,
        )
    )


def phone_hmac(phone: str, secret: str) -> str:
    return hmac.new(secret.encode(), phone.encode(), hashlib.sha256).hexdigest()
