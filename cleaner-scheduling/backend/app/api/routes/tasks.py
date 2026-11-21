from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from app.api.deps import (
    get_clock,
    get_db,
    get_settings,
    require_manager,
    require_manager_idempotency_key,
    require_reader,
)
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.domain.tasks import eligible_for_future_dispatch
from app.domain.timeutil import dump_utc
from app.models import CleaningTask, OperationalException, Property
from app.services.audit import write_audit
from app.services.early_checkout import confirm_early_checkout
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.task_generation import recompute_property_tasks, set_manager_target
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["tasks"])
_MAX_SPAN_DAYS = 93


class TargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_target_at: datetime | None
    reason: str = Field(min_length=1, max_length=500)


class CheckoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm_checkout"]
    confirmed: Literal[True]
    reason: str = Field(min_length=1, max_length=500)


class DurationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_override_minutes: int | None = Field(default=None, gt=0)
    clear_duration_override: bool = False
    bedrooms: int | None = Field(default=None, ge=0)


def task_json(session: Session, task: CleaningTask) -> dict[str, object]:
    return {
        "id": str(task.id),
        "version": task.version,
        "created_at": dump_utc(task.created_at),
        "updated_at": dump_utc(task.updated_at),
        "property_id": str(task.property_id),
        "source_reservation_id": str(task.source_reservation_id),
        "release_at": dump_utc(task.release_at),
        "confirmed_checkout_at": dump_utc(task.confirmed_checkout_at) if task.confirmed_checkout_at else None,
        "deadline_at": dump_utc(task.deadline_at) if task.deadline_at else None,
        "manager_target_at": dump_utc(task.manager_target_at) if task.manager_target_at else None,
        "duration_minutes": task.duration_minutes,
        "duration_source": task.duration_source,
        "status": task.status,
        "completed_at": dump_utc(task.completed_at) if task.completed_at else None,
        "completion_actor": task.completion_actor,
        "dispatchable": _dispatchable(session, task),
    }


@router.get("/tasks")
def list_tasks(
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    status: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = 50,
) -> dict[str, object]:
    if limit < 1 or limit > 100:
        raise AppError("validation_error", "limit must be from 1 to 100.", 422)
    statement = select(CleaningTask).order_by(CleaningTask.release_at, CleaningTask.id)
    if status is not None:
        statement = statement.where(CleaningTask.status == status)
    if from_time is not None or to_time is not None:
        if from_time is None or to_time is None:
            raise AppError("validation_error", "from_time and to_time must be supplied together.", 422)
        if from_time.tzinfo is None or to_time.tzinfo is None:
            raise AppError("validation_error", "Timestamps must include an offset.", 422)
        if to_time <= from_time or (to_time - from_time) > timedelta(days=_MAX_SPAN_DAYS):
            raise AppError("validation_error", "Interval must be positive and at most 93 days.", 422)
        statement = statement.where(CleaningTask.release_at >= from_time, CleaningTask.release_at < to_time)
    rows = session.scalars(statement.limit(limit)).all()
    return {"items": [task_json(session, row) for row in rows], "next_cursor": None}


@router.post("/tasks/{task_id}/target")
def post_target(
    task_id: uuid.UUID,
    payload: TargetBody | CheckoutBody,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    expected = _if_match(request)
    body = payload.model_dump(mode="json")
    digest = request_digest({**body, "if_match": expected} if isinstance(payload, CheckoutBody) else body)
    route = f"/v1/tasks/{task_id}/target"
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route=route,
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request body.", 409)
    result = None
    if isinstance(payload, CheckoutBody):
        task, result = confirm_early_checkout(
            session,
            settings=settings,
            clock=clock,
            task_id=task_id,
            expected_version=expected,
            reason=payload.reason,
            actor=principal.subject,
            request_id=request.state.request_id,
        )
    else:
        task = set_manager_target(
            session,
            clock=clock,
            task_id=task_id,
            expected_version=expected,
            manager_target_at=payload.manager_target_at,
            reason=payload.reason,
            actor=principal.subject,
        )
    response = task_json(session, task)
    if result is not None:
        response["checkout_result"] = result
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route=route,
        key=idempotency_key,
        digest=digest,
        status=200,
        body=response,
        resource_id=task.id,
    )
    session.commit()
    if isinstance(payload, CheckoutBody):
        wake = getattr(request.app.state, "wake_automation", None)
        if wake is not None:
            wake()
    return JSONResponse(status_code=200, content=response)


@router.patch("/properties/{property_id}")
def patch_property_duration(
    property_id: uuid.UUID,
    payload: DurationPatch,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    expected = _if_match(request)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    key = None
    route = f"/v1/properties/{property_id}"
    body = {**payload.model_dump(mode="json"), "if_match": expected}
    digest = request_digest(body)
    if settings.hospitable_read_only:
        if payload.bedrooms is not None:
            raise AppError("source_owned_field", "Bedroom information belongs in Hospitable.", 422)
        key = require_manager_idempotency_key(request, principal, session, clock)
        resolution = resolve_idempotency(
            session, principal_subject=principal.subject, route=route, key=key, digest=digest, clock=clock
        )
        if resolution.outcome == "replay" and resolution.body is not None:
            return resolution.body
        if resolution.outcome == "conflict":
            raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request.", 409)
    prop = session.scalar(select(Property).where(Property.id == property_id).with_for_update())
    if prop is None:
        raise AppError("not_found", "Property was not found.", 404)
    if prop.version != expected:
        raise AppError("stale_version", "Property version is stale.", 409)
    if payload.clear_duration_override:
        prop.duration_override_minutes = None
    elif payload.duration_override_minutes is not None:
        prop.duration_override_minutes = payload.duration_override_minutes
    if payload.bedrooms is not None:
        prop.bedrooms = payload.bedrooms
    prop.version += 1
    prop.updated_at = clock.now()
    recompute_property_tasks(session, clock=clock, property_id=prop.id)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="property.cleaning_time_updated",
        entity_type="property",
        entity_id=prop.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason="Manager updated property cleaning time.",
        redacted_changes={"bedrooms": prop.bedrooms, "duration_override_minutes": prop.duration_override_minutes},
        request_id=request.state.request_id,
    )
    response: dict[str, object] = {
        "id": str(prop.id),
        "version": prop.version,
        "duration_override_minutes": prop.duration_override_minutes,
        "bedrooms": prop.bedrooms,
    }
    if key is not None:
        store_idempotency(
            session,
            settings=settings,
            clock=clock,
            principal_subject=principal.subject,
            route=route,
            key=key,
            digest=digest,
            status=200,
            body=response,
            resource_id=prop.id,
        )
    session.commit()
    return response


def _dispatchable(session: Session, task: CleaningTask) -> bool:
    blocking = session.scalar(
        select(OperationalException.id).where(
            OperationalException.task_id == task.id,
            OperationalException.status.in_(("open", "acknowledged")),
            OperationalException.code.in_(("MISSING_DURATION", "DEADLINE_IMPOSSIBLE", "MISSING_READINESS_BUFFER")),
        )
    )
    return eligible_for_future_dispatch(
        status=task.status,
        duration_minutes=task.duration_minutes,
        blocking_exception_open=blocking is not None,
    )


def _if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None or raw.strip() == "":
        raise AppError("if_match_required", "If-Match is required.", 428)
    value = raw.strip().strip('"')
    if not value.isdigit() or int(value) < 1:
        raise AppError("if_match_invalid", "If-Match must be a positive integer version.", 428)
    return int(value)
