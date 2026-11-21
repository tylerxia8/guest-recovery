from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import timedelta

from app.api.deps import get_clock, get_db, get_settings, require_manager, require_manager_idempotency_key
from app.api.languages import PreferredLanguage, ReadingLanguages
from app.api.routes.local_scheduling import proposed_settings
from app.api.routes.onboarding import onboarding_token
from app.api.routes.schedules import _idempotent
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import Cleaner, CleanerAccessLink, PolicyVersion, Property, ReservationSyncRun
from app.models.accounts import ManagerAccount
from app.models.workspace_connection import WorkspaceConnection
from app.services.audit import write_audit
from app.services.email_addresses import normalize_email
from app.services.field_cipher import protect_text, reveal_text
from app.services.hospitable_sync import sync_hospitable
from app.services.onboarding_email import onboarding_email_status, queue_onboarding_emails
from app.services.policy import approve_policy, create_policy
from app.services.task_generation import recompute_property_tasks
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/workspace", tags=["workspace-setup"])


class ConnectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=16384, repr=False)

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isascii() or any(char.isspace() for char in value):
            raise ValueError("Enter a valid Hospitable access token.")
        return value


class TeamWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    reading_languages: ReadingLanguages = Field(default_factory=list)
    preferred_language: PreferredLanguage | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Enter the cleaner's name.")
        return value.strip()

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        result = normalize_email(value)
        if result is None:
            raise ValueError("Enter the cleaner's email.")
        return result


def enabled(settings: Settings = Depends(get_settings)) -> None:
    if not settings.workspace_signup_enabled:
        raise AppError("not_found", "Not found.", 404)


router.dependencies.append(Depends(enabled))


@router.get("/setup")
def setup_status(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    connection = session.get(WorkspaceConnection, session.info["workspace_id"])
    latest = session.scalar(
        select(ReservationSyncRun)
        .where(ReservationSyncRun.provider == "hospitable")
        .order_by(ReservationSyncRun.started_at.desc())
        .limit(1)
    )
    connected = connection is not None and latest is not None and latest.status == "succeeded"
    return {
        "connected": connected,
        "house_count": session.scalar(select(func.count()).select_from(Property).where(Property.active.is_(True))),
        "cleaner_count": session.scalar(select(func.count()).select_from(Cleaner).where(Cleaner.active.is_(True))),
        "completed": connection is not None and connection.completed_at is not None,
    }


@router.post("/hospitable")
def connect_hospitable(
    payload: ConnectionWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    if not settings.workspace_internal_pat_enabled:
        if not settings.workspace_pilot_pat_enabled:
            raise AppError("connection_not_configured", "Hospitable partner authorization is not configured yet.", 503)
        email = session.scalar(
            select(ManagerAccount.email).where(ManagerAccount.workspace_id == session.info["workspace_id"])
        )
        if email != settings.workspace_invite_email:
            raise AppError("connection_not_available", "This workspace is not enrolled in the private pilot.", 403)

    def action() -> dict[str, object]:
        workspace_id = session.info["workspace_id"]
        connection = session.get(WorkspaceConnection, workspace_id)
        if connection is None:
            connection = WorkspaceConnection(workspace_id=workspace_id, token_ciphertext=b"", connected_at=clock.now())
            session.add(connection)
        connection.token_ciphertext = protect_text(payload.token, settings.field_encryption_secret)
        session.flush()
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="workspace.hospitable_credential_saved",
            entity_type="workspace",
            entity_id=workspace_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Saved an encrypted credential for internal GET-only access.",
            redacted_changes={"upstream_writes": 0},
            request_id=request.state.request_id,
        )
        return {"saved": True}

    _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/workspace/hospitable",
        {"token_digest": hashlib.sha256(payload.token.encode()).hexdigest()},
        action,
        200,
    )
    result = sync_hospitable(session, settings=settings, clock=clock, actor=principal.subject, key=f"connect-{key}")
    if result["status"] != "succeeded":
        raise AppError(
            "connection_failed", "Could not read your Hospitable data. Check token access and try again.", 422
        )
    return JSONResponse(content=setup_status(principal, session))


@router.get("/cleaners")
def list_cleaners(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    links: dict[uuid.UUID, str] = {}
    for grant in session.scalars(
        select(CleanerAccessLink)
        .where(
            CleanerAccessLink.purpose == "onboarding",
            CleanerAccessLink.revoked_at.is_(None),
            CleanerAccessLink.expires_at > clock.now(),
        )
        .order_by(CleanerAccessLink.created_at.desc(), CleanerAccessLink.id)
    ):
        token = onboarding_token(grant.id, settings, session.info["workspace_id"])
        if hmac.compare_digest(grant.token_hash, hashlib.sha256(token.encode()).hexdigest()):
            links.setdefault(grant.cleaner_id, f"{settings.cleaner_portal_base_url}/onboarding#{token}")
    return {
        "cleaners": [
            {
                "id": str(cleaner.id),
                "name": cleaner.display_name,
                "reading_languages": cleaner.reading_languages,
                "preferred_language": cleaner.preferred_language,
                "email": reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
                if cleaner.email_ciphertext
                else None,
                "onboarding_url": links.get(cleaner.id),
            }
            for cleaner in session.scalars(
                select(Cleaner).where(Cleaner.active.is_(True)).order_by(Cleaner.created_at, Cleaner.id)
            )
        ]
    }


@router.post("/cleaners")
def add_cleaner(
    payload: TeamWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        if not setup_status(principal, session)["connected"]:
            raise AppError("connection_required", "Connect Hospitable before adding cleaners.", 409)
        identity, grant_id = uuid.uuid4(), uuid.uuid4()
        session.add(
            Cleaner(
                id=identity,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name=payload.name,
                reading_languages=list(payload.reading_languages),
                preferred_language=payload.preferred_language,
                phone_ciphertext=b"",
                phone_lookup_hmac=hashlib.sha256(f"pending-phone:{identity}".encode()).hexdigest(),
                email_ciphertext=protect_text(payload.email, settings.field_encryption_secret),
                active=True,
                sms_consent_status="pending",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
        )
        session.flush()
        token = onboarding_token(grant_id, settings, session.info["workspace_id"])
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=identity,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="cleaner.created",
            entity_type="cleaner",
            entity_id=identity,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Added cleaner during workspace setup.",
            redacted_changes={"email_stored": True},
            request_id=request.state.request_id,
        )
        return {"id": str(identity), "grant_id": str(grant_id)}

    response = _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/workspace/cleaners",
        payload.model_dump(exclude={"reading_languages", "preferred_language"} - payload.model_fields_set),
        action,
        201,
    )
    import json

    result = json.loads(bytes(response.body))
    grant = session.get(CleanerAccessLink, uuid.UUID(result["grant_id"]))
    cleaner = session.get(Cleaner, uuid.UUID(result["id"]))
    if (
        grant is None
        or cleaner is None
        or not cleaner.active
        or grant.revoked_at is not None
        or grant.expires_at <= clock.now()
    ):
        raise AppError("link_unavailable", "Create a fresh onboarding link from Cleaners.", 409)
    token = onboarding_token(grant.id, settings, session.info["workspace_id"])
    return JSONResponse(
        status_code=201,
        content={
            "id": str(cleaner.id),
            "name": cleaner.display_name,
            "reading_languages": cleaner.reading_languages,
            "preferred_language": cleaner.preferred_language,
            "email": payload.email,
            "onboarding_url": f"{settings.cleaner_portal_base_url}/onboarding#{token}",
        },
    )


@router.post("/complete")
def complete_setup(
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        status = setup_status(principal, session)
        if not status["connected"] or not status["cleaner_count"]:
            raise AppError("setup_incomplete", "Connect Hospitable and add at least one cleaner.", 409)
        queue_onboarding_emails(session, settings, clock)
        policy = session.scalar(select(PolicyVersion).where(PolicyVersion.is_active.is_(True)))
        if policy is None:
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
        connection = session.get(WorkspaceConnection, session.info["workspace_id"])
        if connection is None:
            raise RuntimeError("Connected workspace is missing its connection")
        connection.completed_at = clock.now()
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="workspace.setup_completed",
            entity_type="workspace",
            entity_id=connection.workspace_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Manager finished setup using standard scheduling defaults.",
            redacted_changes={"defaults_approved": True},
            request_id=request.state.request_id,
        )
        session.flush()
        return {"completed": True, "onboarding_emails": onboarding_email_status(session)}

    response = _idempotent(request, principal, session, settings, clock, key, "/v1/workspace/complete", {}, action, 200)
    if settings.local_email_automation_enabled:
        request.app.state.wake_automation()
    return response


@router.get("/onboarding-emails")
def onboarding_email_deliveries(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return {"counts": onboarding_email_status(session), "enabled": settings.gmail_onboarding_send_enabled}
