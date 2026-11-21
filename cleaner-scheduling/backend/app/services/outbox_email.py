from __future__ import annotations

import json
import logging
import smtplib
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import cast

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models import OperationalException, OutboxEvent
from app.services.email_addresses import normalize_email
from app.services.field_cipher import reveal_text
from app.services.gmail import connect, send_connected_email
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id

logger = logging.getLogger(__name__)

STUCK_SENDING_THRESHOLD_MINUTES = 5


def read_outbox_email(event: OutboxEvent, settings: Settings) -> dict[str, str]:
    snapshot = json.loads(reveal_text(bytes.fromhex(str(event.payload["snapshot"])), settings.field_encryption_secret))
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"recipient", "subject", "body"}
        or not all(isinstance(value, str) for value in snapshot.values())
        or normalize_email(snapshot["recipient"]) is None
    ):
        raise ValueError("Invalid outbox email snapshot")
    return cast(dict[str, str], snapshot)


def claim_outbox_email(
    session: Session, event: OutboxEvent, settings: Settings, clock: Clock, prepare: Callable[[], dict[str, str]]
) -> dict[str, str] | None:
    """Claim an outbox email for delivery (runs under lock).

    Returns the snapshot if claim succeeds, None if claim fails.
    After this returns successfully, the row is claimed and the caller should
    commit to release the lock before calling connect().
    """
    if event.payload.get("status") == "sending":
        _finish(session, event, "uncertain", "interrupted_send", clock)
        return None
    if event.payload.get("status") != "queued":
        _finish(session, event, "failed", "invalid_state", clock)
        return None
    claimed_event_id = event.id
    claimed_version = event.version
    try:
        snapshot = prepare()
    except AppError as error:
        _finish(session, event, "cancelled", error.code, clock)
        return None
    except (ValueError, KeyError, InvalidTag):
        _finish(session, event, "failed", "invalid_snapshot", clock)
        return None
    except Exception:
        session.rollback()
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        reloaded = session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == claimed_event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if reloaded is None or reloaded.payload.get("status") != "queued" or reloaded.version != claimed_version:
            logger.warning(
                "outbox_email.claim_guard event_id=%s expected_version=%s actual_version=%s actual_status=%s",
                claimed_event_id,
                claimed_version,
                reloaded.version if reloaded else None,
                reloaded.payload.get("status") if reloaded else None,
            )
            session.rollback()
            return None
        reloaded.attempt_count += 1
        if reloaded.attempt_count < 3:
            reloaded.available_at = clock.now() + timedelta(seconds=60 * 2 ** (reloaded.attempt_count - 1))
            reloaded.updated_at = clock.now()
            reloaded.last_error_code = "pre_send_error"
            reloaded.version += 1
            session.commit()
        else:
            _finish(session, reloaded, "failed", "pre_send_error", clock)
        return None
    event.attempt_count += 1
    event.payload = {**event.payload, "status": "sending"}
    event.claimed_until = clock.now() + timedelta(minutes=5)
    event.updated_at = clock.now()
    event.version += 1
    return snapshot


def deliver_outbox_email(
    session: Session, event: OutboxEvent, settings: Settings, clock: Clock, prepare: Callable[[], dict[str, str]]
) -> None:
    """Deliver an outbox email with proper error handling.

    H3 fix: Errors that occur BEFORE SMTP submission actually starts should re-queue
    or cancel the delivery, not mark it 'uncertain'. Only mark 'uncertain' once SMTP
    submission has actually started (submitted=True).

    If a row goes 'uncertain', create a manager-visible operational exception.
    """
    snapshot = claim_outbox_email(session, event, settings, clock, prepare)
    if snapshot is None:
        return
    event_id = event.id
    topic = event.topic
    attempt_count = event.attempt_count
    expected_version = event.version
    session.commit()
    client = None
    submitted = False
    smtp_error: Exception | None = None
    try:
        client, address = connect(settings.gmail_credentials_file)
        pre_send_event = session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            pre_send_event is None
            or pre_send_event.payload.get("status") != "sending"
            or pre_send_event.version != expected_version
        ):
            logger.warning(
                "outbox_email.pre_send_guard event_id=%s expected_version=%s actual_version=%s actual_status=%s",
                event_id,
                expected_version,
                pre_send_event.version if pre_send_event else None,
                pre_send_event.payload.get("status") if pre_send_event else None,
            )
            client.close()
            return
        pre_send_event.payload = {**pre_send_event.payload, "submitting_at": clock.now().isoformat()}
        pre_send_event.version += 1
        session.commit()
        expected_version += 1
        submitted = True
        send_connected_email(
            client,
            address,
            f"security-{event_id}" if topic == "security.email_alert" else f"outbox-{event_id}",
            **snapshot,
        )
    except Exception as exc:
        smtp_error = exc
    finally:
        if client is not None:
            client.close()

    final_event = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if final_event is None or final_event.payload.get("status") != "sending" or final_event.version != expected_version:
        logger.warning(
            "outbox_email.final_guard event_id=%s expected_version=%s actual_version=%s actual_status=%s",
            event_id,
            expected_version,
            final_event.version if final_event else None,
            final_event.payload.get("status") if final_event else None,
        )
        return
    if submitted:
        if smtp_error is None:
            _finish(session, final_event, "accepted", None, clock)
        elif isinstance(smtp_error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
            _finish(session, final_event, "failed", "message_rejected", clock)
        else:
            _finish(session, final_event, "uncertain", "send_outcome_unknown", clock)
    else:
        permanent = isinstance(smtp_error, ValueError) if smtp_error else False
        if permanent or attempt_count >= 3:
            _finish(session, final_event, "failed", "connection_failed", clock)
        else:
            final_event.payload = {**final_event.payload, "status": "queued"}
            final_event.available_at = clock.now() + timedelta(seconds=60 * 2 ** (attempt_count - 1))
            final_event.claimed_until = None
            final_event.updated_at = clock.now()
            final_event.last_error_code = "connection_unavailable"
            final_event.version += 1
            session.commit()
            logger.warning("outbox_email.retry event_id=%s topic=%s attempt=%s", event_id, topic, attempt_count)


def _open_problem(
    session: Session,
    clock: Clock,
    code: str,
    key: str,
    details: dict[str, object],
) -> None:
    """Create or update an operational exception for manager visibility."""
    now = clock.now()
    existing = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == key,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if existing is not None:
        existing.details = details
        existing.updated_at = now
        existing.version += 1
        return
    exception = OperationalException(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        code=code,
        dedupe_key=key,
        sync_run_id=None,
        status="open",
        severity="warning",
        details=details,
        opened_at=now,
        resolved_at=None,
        resolution_reason=None,
    )
    session.add(exception)


def _finish(session: Session, event: OutboxEvent, status: str, error_code: str | None, clock: Clock) -> None:
    event.payload = {**event.payload, "status": status}
    event.processed_at = clock.now()
    event.updated_at = clock.now()
    event.claimed_until = None
    event.last_error_code = error_code
    event.version += 1

    if status == "uncertain":
        _open_problem(
            session,
            clock,
            "OUTBOX_EMAIL_UNCERTAIN",
            f"outbox-email:{event.id}",
            {
                "event_id": str(event.id),
                "topic": event.topic,
                "kind": event.payload.get("kind"),
                "error_code": error_code,
            },
        )
    elif status == "failed":
        _open_problem(
            session,
            clock,
            "EMAIL_DELIVERY_FAILED",
            f"outbox-email:{event.id}",
            {
                "event_id": str(event.id),
                "topic": event.topic,
                "kind": event.payload.get("kind"),
                "error_code": error_code,
            },
        )

    session.commit()
    logger.info(
        "outbox_email.delivery event_id=%s topic=%s kind=%s status=%s error_code=%s",
        event.id,
        event.topic,
        event.payload.get("kind"),
        status,
        error_code,
    )


def recover_stuck_outbox_emails(session: Session, clock: Clock, topic: str) -> int:
    """Recover outbox emails stuck in 'sending' status.

    H2/H3 fix: If a row has been stuck in 'sending' status and the lease
    has expired (claimed_until < now), either re-queue it (if no submitting_at
    marker exists - meaning we crashed before SMTP) or mark it 'uncertain'
    (if submitting_at exists - meaning we may have submitted).

    Returns the count of recovered rows.
    """
    now = clock.now()
    recovered = 0

    for event in session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.topic == topic,
            OutboxEvent.processed_at.is_(None),
            OutboxEvent.payload["status"].as_string() == "sending",
            OutboxEvent.claimed_until < now,
        )
        .with_for_update()
    ):
        has_submitting_marker = event.payload.get("submitting_at") is not None
        if has_submitting_marker:
            event.payload = {**event.payload, "status": "uncertain"}
            event.processed_at = now
            event.last_error_code = "interrupted_send"
            _open_problem(
                session,
                clock,
                "OUTBOX_EMAIL_UNCERTAIN",
                f"outbox-email:{event.id}",
                {
                    "event_id": str(event.id),
                    "topic": event.topic,
                    "kind": event.payload.get("kind"),
                    "error_code": "interrupted_send",
                },
            )
            logger.warning("outbox_email.stuck_uncertain event_id=%s topic=%s", event.id, event.topic)
        else:
            if event.attempt_count < 3:
                event.payload = {**event.payload, "status": "queued"}
                event.available_at = now + timedelta(seconds=60 * 2 ** (event.attempt_count - 1))
                event.last_error_code = "recovered_pre_send"
                logger.info("outbox_email.stuck_requeued event_id=%s topic=%s", event.id, event.topic)
            else:
                event.payload = {**event.payload, "status": "failed"}
                event.processed_at = now
                event.last_error_code = "max_attempts_pre_send"
                _open_problem(
                    session,
                    clock,
                    "EMAIL_DELIVERY_FAILED",
                    f"outbox-email:{event.id}",
                    {
                        "event_id": str(event.id),
                        "topic": event.topic,
                        "kind": event.payload.get("kind"),
                        "error_code": "max_attempts_pre_send",
                    },
                )
                logger.warning("outbox_email.stuck_failed event_id=%s topic=%s", event.id, event.topic)
        event.updated_at = now
        event.claimed_until = None
        event.version += 1
        recovered += 1

    if recovered > 0:
        session.commit()
    return recovered
