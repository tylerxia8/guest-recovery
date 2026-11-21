from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import timedelta

import redis
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.errors import AdapterError
from app.adapters.reservations.hospitable_read import HospitableReadClient, SourceMessage
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models import CleaningTask, OperationalException, OutboxEvent, Reservation
from app.models.workspace_connection import WorkspaceConnection
from app.services.checkout_policy import baseline_checkout, departure_message
from app.services.early_checkout import confirm_message_checkout, invalidate_message_checkout, review_message_checkout
from app.services.field_cipher import reveal_text
from app.services.reservation_sync import _open_exception, publication_blocked
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID

logger = logging.getLogger(__name__)
_POLL = "hospitable.guest_departure_poll"
_EVIDENCE = "hospitable.guest_departure_evidence"


def run_guest_departure_sync(factory: sessionmaker[Session], settings: Settings, clock: Clock) -> int:
    if not (
        settings.hospitable_guest_departure_enabled
        and settings.hosted_workspace
        and settings.local_scheduling_enabled
        and settings.hospitable_read_only
    ):
        return 0
    with factory() as session:
        workspace_id = session.info.get("workspace_id", LEGACY_WORKSPACE_ID)
        connection = session.get(WorkspaceConnection, workspace_id)
        if connection is None or connection.completed_at is None or workspace_id == LEGACY_WORKSPACE_ID:
            return 0
        if publication_blocked(session, clock=clock, settings=settings):
            return 0
        if not session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": f"guest-departure-claim:{workspace_id}"},
        ):
            return 0
        now = clock.now()
        rows = session.execute(
            select(Reservation, CleaningTask, OutboxEvent)
            .join(CleaningTask, CleaningTask.source_reservation_id == Reservation.id)
            .outerjoin(
                OutboxEvent,
                (OutboxEvent.aggregate_id == Reservation.id) & (OutboxEvent.topic == _POLL),
            )
            .where(
                Reservation.provider == "hospitable",
                Reservation.status == "active",
                Reservation.checkin_at <= now,
                Reservation.checkout_at > now - timedelta(hours=24),
                CleaningTask.status.not_in(("completed", "canceled")),
                or_(
                    OutboxEvent.id.is_(None),
                    OutboxEvent.available_at <= now,
                    OutboxEvent.last_error_code.in_(("message_fields_invalid", "message_text_unavailable")),
                ),
            )
            .order_by(func.coalesce(OutboxEvent.available_at, Reservation.observed_at), Reservation.id)
            .limit(20)
        ).all()
        claimed: list[tuple[Reservation, CleaningTask, uuid.UUID]] = []
        for reservation, task, checkpoint in rows:
            if checkpoint is None:
                checkpoint = OutboxEvent(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    topic=_POLL,
                    aggregate_id=reservation.id,
                    aggregate_version=reservation.version,
                    intent_key=f"guest-departure-poll:{reservation.id}",
                    payload={},
                    available_at=now + timedelta(minutes=5),
                    attempt_count=0,
                )
                session.add(checkpoint)
            else:
                checkpoint.available_at = now + timedelta(minutes=5)
                checkpoint.updated_at = now
                checkpoint.version += 1
            if checkpoint.last_error_code in {"message_fields_invalid", "message_text_unavailable"}:
                checkpoint.last_error_code = "message_retry_pending"
            claimed.append((reservation, task, checkpoint.id))
        unreadable = session.scalar(
            select(OutboxEvent.id)
            .join(Reservation, Reservation.id == OutboxEvent.aggregate_id)
            .join(CleaningTask, CleaningTask.source_reservation_id == Reservation.id)
            .where(
                OutboxEvent.topic == _POLL,
                OutboxEvent.last_error_code.is_not(None),
                Reservation.provider == "hospitable",
                Reservation.status == "active",
                Reservation.checkin_at <= now,
                Reservation.checkout_at > now - timedelta(hours=24),
                CleaningTask.status.not_in(("completed", "canceled")),
            )
            .limit(1)
        )
        if unreadable is None:
            for dedupe_key in ("guest-message-read-unavailable", "guest-message-unexpected-error"):
                warning = session.scalar(
                    select(OperationalException).where(
                        OperationalException.dedupe_key == dedupe_key,
                        OperationalException.status.in_(("open", "acknowledged")),
                    )
                )
                if warning is not None:
                    warning.status = "resolved"
                    warning.resolved_at = now
                    warning.updated_at = now
                    warning.resolution_reason = "Current guest message reads recovered."
                    warning.version += 1
                    logger.info(
                        "guest_departure.read_recovered workspace_id=%s dedupe_key=%s", workspace_id, dedupe_key
                    )
        ciphertext = connection.token_ciphertext
        session.commit()
    if not claimed:
        return 0
    changed = 0
    cache = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        for reservation, task, checkpoint_id in claimed:
            try:
                external_id = str(uuid.UUID(reservation.external_id))
                if not cache.set(f"turnover:guest-departure:{external_id}", "1", nx=True, ex=300):
                    continue
                client = HospitableReadClient(
                    settings.hospitable_token_file,
                    access_token=reveal_text(ciphertext, settings.field_encryption_secret),
                )
                try:
                    messages = client.guest_messages(external_id)
                finally:
                    client.close()
                applied = _apply_messages(
                    factory, settings, clock, reservation, task, checkpoint_id, ciphertext, messages
                )
                changed += applied
                logger.info(
                    "guest_departure.poll_complete workspace_id=%s messages=%s changes=%s",
                    workspace_id,
                    len(messages),
                    applied,
                )
            except Exception as error:
                is_expected = isinstance(error, (AppError, AdapterError))
                error_code = type(error).__name__
                exception_code = "GUEST_MESSAGE_READ_UNAVAILABLE"
                if isinstance(error, AppError):
                    error_code = error.code
                elif isinstance(error, AdapterError):
                    error_code = {
                        "Hospitable token must be an owner-only regular file.": "token_permissions_invalid",
                        "Hospitable token file is invalid.": "token_format_invalid",
                        "Hospitable message sender is invalid.": "message_sender_invalid",
                        "Hospitable guest message is invalid.": "message_fields_invalid",
                        "Hospitable guest message identity is invalid.": "message_identity_invalid",
                        "Hospitable guest message text is unavailable.": "message_text_unavailable",
                        "Hospitable guest message text is missing.": "message_text_missing",
                        "Hospitable guest message text format is invalid.": "message_text_format_invalid",
                        "Hospitable guest message text exceeded the read limit.": "message_text_oversized",
                        "Hospitable guest message timestamp is invalid.": "message_timestamp_invalid",
                        "Hospitable guest message timestamp is ambiguous.": "message_timezone_missing",
                        "Hospitable returned duplicate guest messages.": "message_identity_duplicate",
                        (
                            "Hospitable messages exceeded the read limit or have an invalid schema."
                        ): "message_list_invalid",
                        "Hospitable returned invalid JSON.": "provider_json_invalid",
                        "Hospitable response is not an object.": "provider_shape_invalid",
                        "Hospitable response exceeded the read limit.": "provider_read_limit",
                        "Hospitable connection failed.": "provider_connection_failed",
                    }.get(str(error), "provider_unavailable")
                    status = re.fullmatch(r"Hospitable read failed with HTTP (\d{3})\.", str(error))
                    if status is not None:
                        error_code = f"hospitable_http_{status.group(1)}"
                else:
                    error_code = f"unexpected_{type(error).__name__}"
                    exception_code = "GUEST_MESSAGE_UNEXPECTED_ERROR"
                try:
                    with factory() as session:
                        failed_checkpoint = session.get(OutboxEvent, checkpoint_id)
                        if failed_checkpoint is not None:
                            failed_checkpoint.attempt_count += 1
                            failed_checkpoint.last_error_code = error_code
                            backoff_minutes = 5 if is_expected else 1
                            failed_checkpoint.available_at = clock.now() + timedelta(
                                minutes=min(60, backoff_minutes * 2 ** min(failed_checkpoint.attempt_count, 4))
                            )
                            failed_checkpoint.updated_at = clock.now()
                            failed_checkpoint.version += 1
                        _open_exception(
                            session,
                            clock=clock,
                            code=exception_code,
                            dedupe_key="guest-message-read-unavailable"
                            if is_expected
                            else "guest-message-unexpected-error",
                            sync_run_id=None,
                            details={"error": error_code},
                            severity="warning" if is_expected else "critical",
                        )
                        session.commit()
                except Exception as record_error:
                    logger.error(
                        "guest_departure.error_recording_failed workspace_id=%s exception=%s",
                        workspace_id,
                        type(record_error).__name__,
                        exc_info=True,
                    )
                if is_expected:
                    logger.warning(
                        "guest_departure.read_unavailable workspace_id=%s error_code=%s exception=%s",
                        workspace_id,
                        error_code,
                        type(error).__name__,
                    )
                else:
                    logger.error(
                        "guest_departure.unexpected_error workspace_id=%s error_code=%s exception=%s",
                        workspace_id,
                        error_code,
                        type(error).__name__,
                        exc_info=True,
                    )
    finally:
        cache.close()
    return changed


def _apply_messages(
    factory: sessionmaker[Session],
    settings: Settings,
    clock: Clock,
    snapshot: Reservation,
    task_snapshot: CleaningTask,
    checkpoint_id: uuid.UUID,
    ciphertext: bytes,
    messages: list[SourceMessage],
) -> int:
    with factory() as session:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        reservation = session.get(Reservation, snapshot.id, with_for_update=True)
        task = session.get(CleaningTask, task_snapshot.id, with_for_update=True)
        checkpoint = session.get(OutboxEvent, checkpoint_id, with_for_update=True)
        connection = session.get(WorkspaceConnection, get_workspace_id(session))
        now = clock.now()
        if (
            reservation is None
            or task is None
            or checkpoint is None
            or connection is None
            or connection.completed_at is None
            or connection.token_ciphertext != ciphertext
            or reservation.version != snapshot.version
            or reservation.checkin_at != snapshot.checkin_at
            or reservation.checkout_at != snapshot.checkout_at
            or reservation.status != "active"
            or task.version != task_snapshot.version
            or task.status in {"completed", "canceled"}
            or publication_blocked(session, clock=clock, settings=settings)
            or not reservation.checkin_at <= now < reservation.checkout_at + timedelta(hours=24)
        ):
            return 0
        baseline = baseline_checkout(session, reservation)
        candidates = []
        for message in messages:
            if not max(reservation.checkin_at, reservation.updated_at) <= message.sent_at <= now:
                continue
            decision = departure_message(message.body)
            if decision != "unknown":
                candidates.append((message, decision))
        candidates.sort(key=lambda item: (item[0].sent_at, item[1] == "still_present"))
        changed = 0
        for message, decision in candidates:
            digest = hashlib.sha256(message.external_id.encode()).hexdigest()
            intent = f"guest-departure-evidence:{reservation.id}:{digest}"
            if session.scalar(select(OutboxEvent.id).where(OutboxEvent.intent_key == intent)) is not None:
                continue
            session.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    topic=_EVIDENCE,
                    aggregate_id=reservation.id,
                    aggregate_version=reservation.version,
                    intent_key=intent,
                    payload={"message_hash": digest, "sent_at": message.sent_at.isoformat(), "decision": decision},
                    available_at=now,
                    processed_at=now,
                    attempt_count=0,
                )
            )
            payload = dict(checkpoint.payload)
            previous_at = str(payload.get("latest_at", ""))
            if previous_at and (
                message.sent_at.isoformat() < previous_at
                or (
                    message.sent_at.isoformat() == previous_at
                    and payload.get("latest_decision") == "still_present"
                    and decision == "departed"
                )
            ):
                continue
            payload.update({"latest_at": message.sent_at.isoformat(), "latest_decision": decision})
            automatic = (
                task.confirmed_checkout_at is not None
                and payload.get("confirmed_at") == task.confirmed_checkout_at.isoformat()
                and payload.get("confirmed_version") == reservation.version
            )
            request_id = str(uuid.uuid4())
            if (
                decision == "departed"
                and task.confirmed_checkout_at is None
                and message.sent_at < baseline
                and now < baseline
            ):
                task, _result = confirm_message_checkout(
                    session,
                    settings=settings,
                    clock=clock,
                    task_id=task.id,
                    expected_version=task.version,
                    departed_at=message.sent_at,
                    reason="Guest message confirmed completed departure",
                    actor="guest-departure-worker",
                    request_id=request_id,
                )
                payload.update(
                    {
                        "confirmed_at": task.confirmed_checkout_at.isoformat() if task.confirmed_checkout_at else None,
                        "confirmed_version": reservation.version,
                    }
                )
                changed += 1
            elif (
                decision == "still_present"
                and automatic
                and task.confirmed_checkout_at is not None
                and message.sent_at >= task.confirmed_checkout_at
            ):
                task = invalidate_message_checkout(
                    session,
                    settings=settings,
                    clock=clock,
                    task_id=task.id,
                    expected_version=task.version,
                    reason="Later guest message contradicts departure",
                    actor="guest-departure-worker",
                    request_id=request_id,
                )
                payload.pop("confirmed_at", None)
                payload.pop("confirmed_version", None)
                changed += 1
            elif decision == "still_present" and (
                (task.confirmed_checkout_at is not None and message.sent_at >= task.confirmed_checkout_at)
                or (task.confirmed_checkout_at is None and message.sent_at >= baseline)
            ):
                task = review_message_checkout(
                    session,
                    settings=settings,
                    clock=clock,
                    task_id=task.id,
                    expected_version=task.version,
                    reason="Guest message reports occupancy after checkout release",
                    actor="guest-departure-worker",
                    request_id=request_id,
                )
                changed += 1
            checkpoint.payload = payload
            session.flush()
        checkpoint.attempt_count = 0
        checkpoint.last_error_code = None
        checkpoint.updated_at = now
        checkpoint.version += 1
        session.commit()
        return changed
