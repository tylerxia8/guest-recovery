from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy.orm import Session

from app.clock import Clock
from app.logging_redaction import redact_text
from app.models import AuditLog


def write_audit(
    session: Session,
    *,
    clock: Clock,
    actor_type: str,
    actor_subject: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    correlation_id: uuid.UUID,
    reason: str | None,
    redacted_changes: dict[str, object],
    request_id: str | None,
) -> AuditLog:
    now = clock.now()
    safe_reason = redact_text(reason) if reason else None
    row = AuditLog(
        id=uuid.uuid4(),
        created_at=now,
        actor_type=actor_type,
        actor_subject=actor_subject,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        occurred_at=now,
        reason=safe_reason,
        redacted_changes=cast(dict[str, object], _sanitize(redacted_changes)),
        request_id=request_id,
    )
    session.add(row)
    return row


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {key: _sanitize(item) for key, item in mapping.items()}
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_sanitize(item) for item in items]
    if isinstance(value, str):
        return redact_text(value)
    return value
