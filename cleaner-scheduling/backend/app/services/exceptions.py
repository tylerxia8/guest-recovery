from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.errors import AppError
from app.models import OperationalException
from app.services.audit import write_audit

_UNWAIVABLE = {
    "DEADLINE_IMPOSSIBLE",
    "MISSING_DURATION",
    "MISSING_READINESS_BUFFER",
    "LOCK_CONFLICT",
    "NO_CAPACITY",
    "NO_ROUTE",
    "NO_LOCATION",
    "INELIGIBLE",
}


def update_exception(
    session: Session,
    *,
    clock: Clock,
    exception_id: uuid.UUID,
    expected_version: int,
    status: str | None,
    owner_subject: str | None,
    resolution_reason: str | None,
    actor: str,
) -> OperationalException:
    row = session.get(OperationalException, exception_id)
    if row is None:
        raise AppError("not_found", "Exception was not found.", 404)
    if row.version != expected_version:
        raise AppError("stale_version", "Exception version is stale. Reload and retry.", 409)
    if status is not None and status not in {"open", "acknowledged", "resolved"}:
        raise AppError("validation_error", "Exception status is invalid.", 422)
    if status == "resolved":
        if resolution_reason is None or not resolution_reason.strip():
            raise AppError("validation_error", "Resolving an exception requires a reason.", 422)
        if row.code in _UNWAIVABLE:
            raise AppError(
                "unresolved_cause",
                "This exception cannot be resolved while the underlying condition remains.",
                422,
            )
        row.status = "resolved"
        row.resolution_reason = resolution_reason.strip()
        row.resolved_at = clock.now()
    elif status is not None:
        row.status = status
    if owner_subject is not None:
        row.owner_subject = owner_subject.strip() or None
    row.version += 1
    row.updated_at = clock.now()
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="exception.updated",
        entity_type="exception",
        entity_id=row.id,
        correlation_id=row.id,
        reason=resolution_reason,
        redacted_changes={"status": row.status, "version": row.version, "code": row.code},
        request_id=None,
    )
    session.flush()
    return row
