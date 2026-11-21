from __future__ import annotations

import uuid
from datetime import date
from zoneinfo import ZoneInfo

from app.api.deps import get_clock, get_db, get_settings, require_manager, require_reader
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import OperationalException
from app.services.dashboard import build_daily_operations, build_dashboard
from app.services.exceptions import update_exception
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["dashboard"])


class ExceptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    owner_subject: str | None = Field(default=None, max_length=120)
    resolution_reason: str | None = Field(default=None, max_length=500)


@router.get("/dashboard")
def get_dashboard(
    service_date: date | None = None,
    principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    return build_dashboard(
        session,
        clock=clock,
        settings=settings,
        service_date=service_date or clock.now().astimezone(ZoneInfo("America/New_York")).date(),
        principal=principal,
    )


@router.get("/daily-operations")
def get_daily_operations(
    service_date: date | None = None,
    principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    return build_daily_operations(
        session,
        clock=clock,
        settings=settings,
        service_date=service_date or clock.now().astimezone(ZoneInfo("America/New_York")).date(),
        principal=principal,
    )


@router.get("/exceptions")
def list_exceptions(
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    status: str | None = None,
    severity: str | None = None,
) -> dict[str, object]:
    if status is not None and status not in {"open", "acknowledged", "resolved"}:
        raise AppError("validation_error", "Exception status filter is invalid.", 422)
    if severity is not None and severity not in {"info", "warning", "critical"}:
        raise AppError("validation_error", "Exception severity filter is invalid.", 422)
    statement = select(OperationalException).order_by(OperationalException.opened_at.desc()).limit(100)
    if status is not None:
        statement = statement.where(OperationalException.status == status)
    if severity is not None:
        statement = statement.where(OperationalException.severity == severity)
    rows = session.scalars(statement).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "version": row.version,
                "code": row.code,
                "severity": row.severity,
                "status": row.status,
                "owner_subject": row.owner_subject,
                "opened_at": row.opened_at.isoformat(),
                "task_id": None if row.task_id is None else str(row.task_id),
                "resolution_reason": row.resolution_reason,
            }
            for row in rows
        ]
    }


@router.patch("/exceptions/{exception_id}")
def patch_exception(
    exception_id: uuid.UUID,
    payload: ExceptionPatch,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    expected = _if_match(request)
    row = update_exception(
        session,
        clock=clock,
        exception_id=exception_id,
        expected_version=expected,
        status=payload.status,
        owner_subject=payload.owner_subject,
        resolution_reason=payload.resolution_reason,
        actor=principal.subject,
    )
    session.commit()
    return {
        "id": str(row.id),
        "version": row.version,
        "code": row.code,
        "status": row.status,
        "owner_subject": row.owner_subject,
        "resolution_reason": row.resolution_reason,
    }


def _if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None or raw.strip() == "":
        raise AppError("if_match_required", "If-Match is required.", 428)
    value = raw.strip().strip('"')
    if not value.isdigit() or int(value) < 1:
        raise AppError("if_match_invalid", "If-Match must be a positive integer version.", 428)
    return int(value)
