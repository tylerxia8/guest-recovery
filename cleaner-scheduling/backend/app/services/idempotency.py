from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.models import IdempotencyRecord
from app.services.redaction import redact_for_storage

Outcome = Literal["proceed", "replay", "conflict"]


@dataclass(frozen=True)
class IdempotencyResolution:
    outcome: Outcome
    status: int | None = None
    body: dict[str, object] | None = None


def request_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_idempotency(
    session: Session,
    *,
    principal_subject: str,
    route: str,
    key: str,
    digest: str,
    clock: Clock,
) -> IdempotencyResolution:
    statement = (
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.principal_subject == principal_subject,
            IdempotencyRecord.route == route,
            IdempotencyRecord.key == key,
        )
        .with_for_update()
    )
    record = session.scalar(statement)
    now = clock.now()
    if record is None:
        return IdempotencyResolution("proceed")
    if record.expires_at <= now:
        session.delete(record)
        session.flush()
        return IdempotencyResolution("proceed")
    if record.request_digest != digest:
        return IdempotencyResolution("conflict")
    if record.response_status is None or record.response_body is None:
        return IdempotencyResolution("conflict")
    return IdempotencyResolution("replay", record.response_status, record.response_body)


def store_idempotency(
    session: Session,
    *,
    settings: Settings,
    clock: Clock,
    principal_subject: str,
    route: str,
    key: str,
    digest: str,
    status: int,
    body: dict[str, object],
    resource_id: uuid.UUID | None,
) -> None:
    now = clock.now()
    stored = cast(dict[str, object], redact_for_storage(body))
    session.add(
        IdempotencyRecord(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            principal_subject=principal_subject,
            route=route,
            key=key,
            request_digest=digest,
            response_status=status,
            response_body=stored,
            resource_id=resource_id,
            expires_at=now + timedelta(hours=settings.idempotency_retention_hours),
        )
    )
