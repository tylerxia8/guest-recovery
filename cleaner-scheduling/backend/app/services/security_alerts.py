from __future__ import annotations

import json
import logging
from functools import partial
from typing import Literal, cast
from uuid import UUID, uuid4

import redis
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.db import catalog_session_factory, session_factory
from app.models import OutboxEvent
from app.services.field_cipher import protect_text
from app.services.outbox_email import deliver_outbox_email, read_outbox_email, recover_stuck_outbox_emails
from app.workspaces import Workspace

SecurityAlertKind = Literal["repeated_failed_login", "mfa_enabled", "mfa_disabled", "recovery_code_used"]
logger = logging.getLogger(__name__)
_SUBJECTS = {
    "repeated_failed_login": "Repeated unsuccessful sign-in attempts",
    "mfa_enabled": "Two-factor authentication enabled",
    "mfa_disabled": "Two-factor authentication disabled",
    "recovery_code_used": "Recovery code used",
}


def queue_security_alert(
    session: Session,
    account_id: UUID,
    workspace_id: UUID,
    kind: SecurityAlertKind,
    settings: Settings,
    clock: Clock,
) -> None:
    if not settings.security_alert_email_enabled or not settings.hosted_workspace:
        return
    if kind not in _SUBJECTS or not isinstance(account_id, UUID) or not isinstance(workspace_id, UUID):
        raise ValueError("Invalid security alert metadata")
    context = session.info.get("workspace_id")
    if context is not None and context != workspace_id:
        raise ValueError("Security alert workspace mismatch")
    session.execute(
        text("SELECT set_config('app.workspace_id', :workspace_id, true)"), {"workspace_id": str(workspace_id)}
    )
    now = clock.now()
    event_id = uuid4()
    snapshot = {
        "recipient": settings.security_alert_email,
        "subject": f"Turnover security: {_SUBJECTS[kind]}",
        "body": (
            f"{_SUBJECTS[kind]}.\n\n"
            f"Time (UTC): {now.isoformat()}\nAccount ID: {account_id}\n"
            f"Workspace ID: {workspace_id}\nEvent ID: {event_id}\n\n"
            "If this activity is unexpected, review account access in Turnover.\n"
        ),
    }
    result = session.execute(
        insert(OutboxEvent)
        .values(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="security.email_alert",
            aggregate_id=account_id,
            aggregate_version=1,
            intent_key=f"security-alert:{kind}:{account_id}:{now.strftime('%Y%m%d%H')}",
            payload={
                "kind": kind,
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        .on_conflict_do_nothing(constraint="uq_outbox_events_intent")
        .returning(OutboxEvent.id)
    )
    if result.scalar_one_or_none() is not None:
        logger.info("security_alert.queued event_id=%s kind=%s", event_id, kind)


def record_failed_login(
    session: Session,
    cache: redis.Redis,
    account_id: UUID,
    workspace_id: UUID,
    settings: Settings,
    clock: Clock,
) -> None:
    if not settings.security_alert_email_enabled or not settings.hosted_workspace:
        return
    script = (
        "local count = redis.call('INCR', KEYS[1]); "
        "if count == 1 then redis.call('EXPIRE', KEYS[1], 900) end; return count"
    )
    count = int(cast(int, cache.eval(script, 1, f"security-login-failures:{workspace_id}:{account_id}")))
    if count >= 5:
        queue_security_alert(session, account_id, workspace_id, "repeated_failed_login", settings, clock)


def drain_security_alerts(settings: Settings, clock: Clock) -> None:
    if not settings.security_alert_email_enabled or not settings.hosted_workspace:
        return
    with catalog_session_factory(settings)() as catalog:
        workspaces = list(catalog.scalars(select(Workspace.id).where(Workspace.deleted_at.is_(None))))
    remaining = 20
    for workspace_id in workspaces:
        if remaining == 0:
            break
        try:
            factory = session_factory(settings, workspace_id)
            with factory() as session:
                recover_stuck_outbox_emails(session, clock, "security.email_alert")
            while remaining:
                with factory() as session:
                    now = clock.now()
                    event = session.scalar(
                        select(OutboxEvent)
                        .where(
                            OutboxEvent.topic == "security.email_alert",
                            OutboxEvent.processed_at.is_(None),
                            OutboxEvent.available_at <= now,
                            or_(OutboxEvent.claimed_until.is_(None), OutboxEvent.claimed_until <= now),
                        )
                        .order_by(OutboxEvent.available_at, OutboxEvent.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    if event is None:
                        break
                    remaining -= 1
                    deliver_outbox_email(session, event, settings, clock, partial(read_outbox_email, event, settings))
        except Exception as error:
            logger.error(
                "security_alert.workspace_drain_failed workspace_id=%s exception=%s",
                workspace_id,
                type(error).__name__,
                exc_info=True,
            )
