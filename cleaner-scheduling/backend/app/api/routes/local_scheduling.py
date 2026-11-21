from __future__ import annotations

import uuid

from app.api.deps import get_clock, get_db, get_settings, require_idempotency_key, require_manager, require_owner
from app.api.routes.schedules import _idempotent
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.policy_schema import PolicySettings
from app.domain.principal import Principal
from app.domain.tasks import resolve_duration
from app.models import PolicyVersion, Property
from app.services.audit import write_audit
from app.services.idempotency import request_digest
from app.services.policy import approve_policy, create_policy
from app.services.task_generation import recompute_property_tasks
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/local-scheduling", tags=["scheduling"])


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def proposed_settings(settings: Settings) -> PolicySettings:
    return PolicySettings(
        schema_version=1,
        availability_request_local_time=settings.availability_request_local_time,
        availability_cutoff_local_time=settings.availability_cutoff_local_time,
        dispatch_local_time=settings.dispatch_local_time,
        ack_reminder_minutes=settings.ack_reminder_minutes,
        ack_escalation_minutes=settings.ack_escalation_minutes,
        readiness_buffer_minutes=settings.readiness_buffer_minutes,
        auto_reassign_missing_ack=False,
        pre_arrival_horizon_hours=48,
        duration_minutes_by_bedrooms={str(n): resolve_duration(n, None)[0] for n in range(5)},
        solver_time_limit_seconds=settings.solver_time_limit_seconds,
        reservation_reconcile_minutes=settings.reservation_reconcile_minutes,
        routing_cache_ttl_minutes=settings.routing_cache_ttl_minutes,
        integration_stale_threshold_minutes=settings.integration_stale_threshold_minutes,
        mandatory_return_to_base=False,
    )


@router.get("/policy")
def policy_review(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    if not settings.local_scheduling_enabled:
        raise AppError("local_scheduling_disabled", "Local scheduling is disabled.", 403)
    active = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.is_active.is_(True),
            PolicyVersion.approved_at.is_not(None),
            PolicyVersion.effective_at <= clock.now(),
        )
    )
    values = active.settings if active is not None else proposed_settings(settings).model_dump(mode="json")
    return {"approved": active is not None, "settings": values, "fingerprint": request_digest(values)}


@router.post("/policy")
def policy_approval(
    payload: Approval,
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    if not settings.local_scheduling_enabled or not settings.hospitable_read_only:
        raise AppError("local_scheduling_disabled", "Local scheduling is disabled.", 403)

    def action() -> dict[str, object]:
        review = policy_review(principal, session, settings, clock)
        if review["fingerprint"] != payload.fingerprint:
            raise AppError("policy_changed", "Scheduling rules changed. Refresh and review them again.", 409)
        if review["approved"]:
            return review
        policy = create_policy(
            session, clock=clock, settings=proposed_settings(settings), author_subject=principal.subject
        )
        approve_policy(
            session,
            clock=clock,
            policy_id=policy.id,
            expected_version=policy.version,
            approver=principal.subject,
            effective_at=clock.now(),
        )
        for identity in session.scalars(select(Property.id).where(Property.active.is_(True))):
            recompute_property_tasks(session, clock=clock, property_id=identity)
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="local_scheduling.policy_approved",
            entity_type="policy_version",
            entity_id=policy.id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Owner authorized local scheduling using the current default rules.",
            redacted_changes={"policy_version": policy.version_number, "manual_scheduling_only": True},
            request_id=request.state.request_id,
        )
        return {"approved": True, "settings": policy.settings, "fingerprint": payload.fingerprint}

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/local-scheduling/policy",
        payload.model_dump(),
        action,
        200,
    )
