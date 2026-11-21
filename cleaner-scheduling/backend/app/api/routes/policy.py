from __future__ import annotations

import uuid

from app.api.deps import (
    get_clock,
    get_db,
    get_settings,
    require_idempotency_key,
    require_if_match,
    require_owner,
)
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.policy_schema import ApprovePolicyBody, CreatePolicyBody
from app.domain.principal import Principal
from app.services.audit import write_audit
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.policy import approve_policy, create_policy, serialize_policy
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["policy"])


@router.post("/policy-versions")
def post_policy_version(
    request: Request,
    payload: CreatePolicyBody,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    route = "/v1/policy-versions"
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route=route,
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        session.commit()
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError(
            "idempotency_conflict",
            "Idempotency-Key was reused with a different request body.",
            409,
        )
    policy = create_policy(
        session,
        clock=clock,
        settings=payload.settings,
        author_subject=principal.subject,
    )
    response = serialize_policy(policy)
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route=route,
        key=idempotency_key,
        digest=digest,
        status=201,
        body=response,
        resource_id=policy.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="policy_version.created",
        entity_type="policy_version",
        entity_id=policy.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason=payload.reason,
        redacted_changes={
            "version_number": policy.version_number,
            "schema_version": payload.settings.schema_version,
            "is_active": False,
        },
        request_id=request.state.request_id,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(
            "idempotency_conflict",
            "Idempotency-Key conflicts with another request.",
            409,
        ) from exc
    return JSONResponse(status_code=201, content=response)


@router.post("/policy-versions/{policy_id}/approve")
def post_policy_approval(
    policy_id: uuid.UUID,
    request: Request,
    payload: ApprovePolicyBody,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_idempotency_key),
    expected_version: int = Depends(require_if_match),
) -> JSONResponse:
    route = f"/v1/policy-versions/{policy_id}/approve"
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route=route,
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        session.commit()
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError(
            "idempotency_conflict",
            "Idempotency-Key was reused with a different request body.",
            409,
        )
    policy = approve_policy(
        session,
        clock=clock,
        policy_id=policy_id,
        expected_version=expected_version,
        approver=principal.subject,
        effective_at=payload.effective_at,
    )
    response = serialize_policy(policy)
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
        resource_id=policy.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="policy_version.approved",
        entity_type="policy_version",
        entity_id=policy.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason=payload.approval_note,
        redacted_changes={"version_number": policy.version_number, "is_active": policy.is_active},
        request_id=request.state.request_id,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(
            "state_conflict",
            "Policy approval conflicted with another update.",
            409,
        ) from exc
    return JSONResponse(status_code=200, content=response)
