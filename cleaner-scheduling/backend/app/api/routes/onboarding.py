from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import timedelta

import redis
from app.api.deps import get_clock, get_db, get_redis, get_settings, require_manager, require_manager_idempotency_key
from app.api.routes.schedules import _idempotent
from app.api.routes.setup import WeeklyWrite, get_weekly_hours, save_weekly_hours
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import Cleaner, CleanerAccessLink
from app.services.audit import write_audit
from app.services.rate_limit import enforce_rate_limit
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["cleaner-onboarding"])


class OnboardingLinkWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_hours: int = Field(default=168, ge=1, le=168)


def onboarding_token(grant_id: uuid.UUID, settings: Settings, workspace_id: uuid.UUID = LEGACY_WORKSPACE_ID) -> str:
    identity = grant_id.hex if workspace_id == LEGACY_WORKSPACE_ID else f"{workspace_id.hex}.{grant_id.hex}"
    signature = hmac.new(
        settings.field_encryption_secret.encode(), f"cleaner-onboarding:{identity}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{identity}.{signature}"


@router.post("/cleaners/{cleaner_id}/onboarding-link")
def create_onboarding_link(
    cleaner_id: uuid.UUID,
    payload: OnboardingLinkWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        cleaner = session.get(Cleaner, cleaner_id)
        if cleaner is None or not cleaner.active:
            raise AppError("not_found", "Active cleaner was not found.", 404)
        for prior in session.scalars(
            select(CleanerAccessLink).where(
                CleanerAccessLink.cleaner_id == cleaner_id,
                CleanerAccessLink.purpose == "onboarding",
                CleanerAccessLink.revoked_at.is_(None),
            )
        ):
            prior.revoked_at = clock.now()
        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(hours=payload.ttl_hours),
            revoked_at=None,
            purpose="onboarding",
        )
        session.add(grant)
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="cleaner.onboarding_link_created",
            entity_type="cleaner",
            entity_id=cleaner_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Issued cleaner self-service setup access; previous onboarding links revoked.",
            redacted_changes={"expires_at": grant.expires_at.isoformat()},
            request_id=request.state.request_id,
        )
        session.flush()
        return {"grant_id": str(grant.id), "expires_at": grant.expires_at.isoformat(), "sms_sent": False}

    result = _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/onboarding-link",
        payload.model_dump(mode="json"),
        action,
        201,
    )
    body = json.loads(bytes(result.body))
    grant = session.get(CleanerAccessLink, uuid.UUID(body["grant_id"]))
    cleaner = session.get(Cleaner, cleaner_id)
    if (
        grant is None
        or grant.revoked_at is not None
        or grant.expires_at <= clock.now()
        or cleaner is None
        or not cleaner.active
    ):
        raise AppError("link_unavailable", "This onboarding link is no longer available. Create a new link.", 409)
    return JSONResponse(
        status_code=201,
        content={
            **body,
            "token": onboarding_token(grant.id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID)),
        },
    )


def require_onboarding(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> Principal:
    enforce_rate_limit(cache, clock=clock, subject="cleaner-onboarding", bucket="public", limit=600)
    remote = getattr(request.state, "client_address", "unknown" if request.client is None else request.client.host)
    enforce_rate_limit(cache, clock=clock, subject=f"cleaner-onboarding:{remote}", bucket="public", limit=120)
    authorization = request.headers.get("authorization", "")
    if re.fullmatch(r"Bearer (?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}", authorization) is None:
        raise AppError("link_unavailable", "Open your original onboarding link or ask your manager for a new one.", 401)
    if request.method == "POST":
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    token = authorization.removeprefix("Bearer ")
    grant = session.scalar(
        select(CleanerAccessLink).where(
            CleanerAccessLink.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            CleanerAccessLink.purpose == "onboarding",
        )
    )
    cleaner = None if grant is None else session.get(Cleaner, grant.cleaner_id)
    if (
        grant is None
        or grant.revoked_at is not None
        or grant.expires_at <= clock.now()
        or cleaner is None
        or not cleaner.active
    ):
        raise AppError(
            "link_unavailable", "This link expired or was replaced. Ask your manager for a new onboarding link.", 401
        )
    writing = request.method == "POST"
    enforce_rate_limit(
        cache,
        clock=clock,
        subject=f"onboarding:{cleaner.id}",
        bucket="write" if writing else "read",
        limit=settings.write_rate_limit_per_minute if writing else settings.read_rate_limit_per_minute,
    )
    principal = Principal(subject=f"cleaner:{cleaner.id}:grant:{grant.id}", roles=("cleaner",))
    request.state.principal = principal
    request.state.onboarding_cleaner_id = cleaner.id
    return principal


@router.get("/cleaner-onboarding")
def read_onboarding(
    request: Request,
    principal: Principal = Depends(require_onboarding),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    cleaner = session.get(Cleaner, request.state.onboarding_cleaner_id)
    if cleaner is None:
        raise AppError("link_unavailable", "Cleaner setup is unavailable.", 401)
    return {**get_weekly_hours(cleaner.id, principal, session, settings), "cleaner_name": cleaner.display_name}


@router.post("/cleaner-onboarding")
def write_onboarding(
    payload: WeeklyWrite,
    request: Request,
    principal: Principal = Depends(require_onboarding),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    key = request.headers.get("idempotency-key", "")
    if re.fullmatch(r"[\x21-\x7E]{1,128}", key) is None:
        raise AppError(
            "idempotency_key_invalid", "Idempotency-Key must be 1-128 printable ASCII characters without spaces.", 400
        )
    if payload.starting_address is None or any(day.origin_label is not None for day in payload.days):
        raise AppError("validation_error", "Enter your starting address and regular hours.", 422)
    response = save_weekly_hours(
        request.state.onboarding_cleaner_id, payload, request, principal, session, settings, clock, key
    )
    if settings.workspace_signup_enabled and settings.local_email_automation_enabled:
        request.app.state.wake_automation()
    return response
