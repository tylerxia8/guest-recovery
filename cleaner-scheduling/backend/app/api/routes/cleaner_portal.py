from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated, Literal

import redis
from app.api.deps import _idempotency_header, get_clock, get_db, get_redis, get_settings
from app.api.languages import PreferredLanguage
from app.api.routes.schedules import _idempotent
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import Cleaner
from app.services.audit import write_audit
from app.services.cleaner_coordination import assignment_action, authenticate_portal, claim_offer, portal_view
from app.services.daily_availability import set_tomorrow_availability
from app.services.rate_limit import enforce_rate_limit
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["cleaner-portal"])


class CleanerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "complete", "punt", "claim"]
    assignment_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    expected_version: int = Field(gt=0)

    @model_validator(mode="after")
    def _identity(self) -> CleanerAction:
        if self.action == "claim":
            if self.offer_id is None or self.assignment_id is not None:
                raise ValueError("Claim requires an offer_id only.")
        elif self.assignment_id is None or self.offer_id is not None:
            raise ValueError("This action requires an assignment_id only.")
        return self


class CleanerAvailabilityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["set_availability"]
    service_date: date
    available: bool
    expected_version: int = Field(ge=0)


def require_cleaner_portal(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> Cleaner:
    if not settings.hospitable_read_only or not settings.local_scheduling_enabled:
        raise AppError("portal_disabled", "Cleaner coordination is not enabled.", 403)
    enforce_rate_limit(cache, clock=clock, subject="cleaner-portal", bucket="public", limit=600)
    remote = getattr(request.state, "client_address", "unknown" if request.client is None else request.client.host)
    enforce_rate_limit(cache, clock=clock, subject=f"cleaner-portal:{remote}", bucket="public", limit=120)
    authorization = request.headers.get("authorization", "")
    if re.fullmatch(r"Bearer (?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}", authorization) is None:
        raise AppError("link_unavailable", "Open the private cleaner link in your latest schedule email.", 401)
    if request.method == "POST":
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    cleaner = authenticate_portal(session, settings, clock, authorization.removeprefix("Bearer "))
    bucket = "write" if request.method == "POST" else "read"
    enforce_rate_limit(
        cache, clock=clock, subject=f"cleaner:{cleaner.id}", bucket=bucket, limit=30 if bucket == "write" else 120
    )
    return cleaner


@router.get("/cleaner-portal")
def get_cleaner_portal(
    cleaner: Cleaner = Depends(require_cleaner_portal),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    return portal_view(session, settings, clock, cleaner)


@router.post("/cleaner-portal")
def post_cleaner_action(
    payload: Annotated[CleanerAction | CleanerAvailabilityWrite, Field(discriminator="action")],
    request: Request,
    cleaner: Cleaner = Depends(require_cleaner_portal),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    principal = Principal(subject=f"cleaner:{cleaner.id}", roles=("cleaner",))
    key = _idempotency_header(request, principal, session, clock)

    def action() -> dict[str, object]:
        if isinstance(payload, CleanerAvailabilityWrite):
            return set_tomorrow_availability(
                session,
                clock,
                cleaner,
                payload.service_date,
                payload.available,
                payload.expected_version,
                request.state.request_id,
            )
        if payload.action == "claim":
            if payload.offer_id is None:
                raise RuntimeError("Validated claim has no offer identity.")
            return claim_offer(session, settings, clock, cleaner, payload.offer_id, payload.expected_version)
        if payload.assignment_id is None:
            raise RuntimeError("Validated assignment action has no assignment identity.")
        return assignment_action(
            session, settings, clock, cleaner, payload.action, payload.assignment_id, payload.expected_version
        )

    response = _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/cleaner-portal",
        payload.model_dump(mode="json"),
        action,
        200,
    )
    if isinstance(payload, CleanerAvailabilityWrite):
        wake = getattr(request.app.state, "wake_automation", None)
        if wake is not None:
            wake()
    return response


class CleanerLanguageWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferred_language: PreferredLanguage
    expected_cleaner_version: int = Field(gt=0)


@router.post("/cleaner-portal/language")
def post_cleaner_language(
    payload: CleanerLanguageWrite,
    request: Request,
    cleaner: Cleaner = Depends(require_cleaner_portal),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    principal = Principal(subject=f"cleaner:{cleaner.id}", roles=("cleaner",))
    key = _idempotency_header(request, principal, session, clock)

    def action() -> dict[str, object]:
        current = session.scalar(
            select(Cleaner).where(Cleaner.id == cleaner.id).with_for_update().execution_options(populate_existing=True)
        )
        if current is None or not current.active:
            raise AppError("link_unavailable", "This cleaner link is unavailable.", 401)
        if current.version != payload.expected_cleaner_version:
            raise AppError("stale_version", "Cleaner details changed. Reload before saving.", 409)
        if current.preferred_language != payload.preferred_language:
            current.preferred_language = payload.preferred_language
            current.version += 1
            current.updated_at = clock.now()
            write_audit(
                session,
                clock=clock,
                actor_type="cleaner",
                actor_subject=principal.subject,
                action="cleaner.preferred_language_updated",
                entity_type="cleaner",
                entity_id=current.id,
                correlation_id=uuid.UUID(request.state.request_id),
                reason="Cleaner selected their display language.",
                redacted_changes={"preferred_language": current.preferred_language},
                request_id=request.state.request_id,
            )
        session.flush()
        return {
            "preferred_language": current.preferred_language,
            "reading_languages": current.reading_languages,
            "cleaner_version": current.version,
        }

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/cleaner-portal/language",
        payload.model_dump(mode="json"),
        action,
        200,
    )
