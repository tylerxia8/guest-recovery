from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import redis
from app.adapters.errors import PermanentFailure
from app.adapters.reservations.mock import MOCK_TEST_SIGNATURE
from app.adapters.sms.mock import RecordingSmsGateway
from app.adapters.types import VerifiedSmsEvent
from app.api.deps import (
    get_clock,
    get_db,
    get_redis,
    get_settings,
    require_idempotency_key,
    require_manager,
    require_manager_idempotency_key,
    require_owner,
)
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import SmsMessage, WebhookEvent
from app.services.field_cipher import protect_text
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.rate_limit import enforce_rate_limit
from app.services.sms_lifecycle import (
    apply_status,
    issue_link,
    preview_text,
    read_link,
    receive_inbound,
    set_consent,
)
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["sms"])
_LIMIT = 256 * 1024
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    evidence_ref: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    reason: str = Field(min_length=1, max_length=500)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_hours: int = Field(default=24, ge=1, le=24)


@router.post("/cleaners/{cleaner_id}/consent")
def post_consent(
    cleaner_id: uuid.UUID,
    payload: ConsentBody,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    del request
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    route = f"/v1/cleaners/{cleaner_id}/consent"
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
    cleaner = set_consent(
        session,
        clock=clock,
        cleaner_id=cleaner_id,
        status=payload.status,
        evidence_ref=payload.evidence_ref,
        actor=principal.subject,
    )
    response = {"cleaner_id": str(cleaner.id), "status": cleaner.sms_consent_status, "version": cleaner.version}
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
        resource_id=cleaner.id,
    )
    session.commit()
    return JSONResponse(status_code=201, content=response)


@router.post("/cleaners/{cleaner_id}/links")
def post_link(
    cleaner_id: uuid.UUID,
    payload: LinkBody,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    del idempotency_key
    token = issue_link(
        session,
        clock=clock,
        settings=settings,
        cleaner_id=cleaner_id,
        ttl=timedelta(hours=payload.ttl_hours),
    )
    session.commit()
    return JSONResponse(
        status_code=201,
        content={"expires_in_hours": payload.ttl_hours, "token": token, "actor": principal.subject},
    )


@router.get("/cleaner-links/{token}")
def get_link(
    token: str,
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> dict[str, object]:
    enforce_rate_limit(
        cache,
        clock=clock,
        subject="cleaner-link",
        bucket="read",
        limit=settings.read_rate_limit_per_minute,
    )
    del request
    return read_link(session, clock=clock, token=token)


@router.get("/sms-messages")
def list_messages(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    cleaner_id: uuid.UUID | None = None,
    status: str | None = None,
) -> dict[str, object]:
    statement = select(SmsMessage).order_by(SmsMessage.created_at).limit(100)
    if cleaner_id is not None:
        statement = statement.where(SmsMessage.cleaner_id == cleaner_id)
    if status is not None:
        statement = statement.where(SmsMessage.status == status)
    rows = session.scalars(statement).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "cleaner_id": None if row.cleaner_id is None else str(row.cleaner_id),
                "purpose": row.purpose,
                "status": row.status,
                "accepted_at": None if row.accepted_at is None else row.accepted_at.isoformat(),
                "error_code": row.error_code,
                "schedule_version": row.schedule_version_number,
                "redacted_preview": preview_text(session, settings, row),
                "provider": row.provider,
            }
            for row in rows
        ]
    }


@router.post("/webhooks/twilio/inbound")
async def twilio_inbound(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> Response:
    raw = await _read_limited(request)
    _assert_mock_ingress(settings)
    enforce_rate_limit(
        cache,
        clock=clock,
        subject="sms-ingress",
        bucket="write",
        limit=settings.write_rate_limit_per_minute,
    )
    try:
        event = _verified(raw, request)
    except PermanentFailure as exc:
        raise AppError("validation_error", "Mock SMS envelope was rejected.", 422) from exc
    dedupe = f"mock-sms:{event.message_id}"
    existing = session.scalar(
        select(WebhookEvent).where(WebhookEvent.provider == "mock-sms", WebhookEvent.dedupe_key == dedupe)
    )
    if existing is not None:
        return Response(content=_EMPTY_TWIML, media_type="application/xml")
    now = clock.now()
    inbox = WebhookEvent(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider="mock-sms",
        external_event_id=event.message_id,
        dedupe_key=dedupe,
        payload_digest=dedupe,
        payload_encrypted=protect_text(event.body, settings.field_encryption_secret),
        verified_at=now,
        received_at=now,
        processed_at=now,
        status="processed",
        attempt_count=1,
        error_code=None,
    )
    session.add(inbox)
    session.flush()
    receive_inbound(session, clock=clock, settings=settings, event=event)
    session.commit()
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


@router.post("/webhooks/twilio/status")
async def twilio_status(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> Response:
    raw = await _read_limited(request)
    _assert_mock_ingress(settings)
    enforce_rate_limit(
        cache,
        clock=clock,
        subject="sms-status",
        bucket="write",
        limit=settings.write_rate_limit_per_minute,
    )
    _verified_signature(request)
    fields = _fields(raw)
    message_id = fields.get("message_id", "")
    status = fields.get("status", "unknown")
    error = fields.get("error_code") or None
    dedupe = f"mock-sms-status:{message_id}:{status}:{error or ''}"
    existing = session.scalar(
        select(WebhookEvent).where(WebhookEvent.provider == "mock-sms", WebhookEvent.dedupe_key == dedupe)
    )
    if existing is None:
        now = clock.now()
        session.add(
            WebhookEvent(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                provider="mock-sms",
                external_event_id=message_id or None,
                dedupe_key=dedupe,
                payload_digest=dedupe,
                payload_encrypted=None,
                verified_at=now,
                received_at=now,
                processed_at=now,
                status="processed",
                attempt_count=1,
                error_code=None,
            )
        )
        from app.adapters.types import DeliveryUpdate

        apply_status(
            session,
            clock=clock,
            update=DeliveryUpdate(provider_message_id=message_id, status=status, error_code=error),
        )
        session.commit()
    return Response(status_code=204)


def _assert_mock_ingress(settings: Settings) -> None:
    if settings.sms_provider != "mock" or settings.twilio_auth_token or settings.twilio_account_sid:
        raise AppError("capability_unavailable", "SMS ingress stays on the mock recorder.", 403)


async def _read_limited(request: Request) -> bytes:
    raw = await request.body()
    if len(raw) > _LIMIT:
        raise AppError("payload_too_large", "Webhook body exceeds 256 KB.", 413)
    return raw


def _verified(raw: bytes, request: Request) -> VerifiedSmsEvent:
    _verified_signature(request)
    return RecordingSmsGateway().verify_inbound(raw, dict(request.headers), str(request.url))


def _verified_signature(request: Request) -> None:
    signature = request.headers.get("x-mock-signature", "")
    if signature != MOCK_TEST_SIGNATURE:
        raise AppError("invalid_signature", "Webhook signature was rejected.", 401)


def _fields(raw: bytes) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed
