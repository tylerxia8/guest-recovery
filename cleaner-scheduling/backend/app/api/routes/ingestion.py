from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.reservations.calendar import assert_feed_url_allowed
from app.adapters.reservations.import_csv import MAX_BYTES, parse_reservation_csv
from app.adapters.types import NormalizedReservation
from app.api.deps import (
    get_clock,
    get_db,
    get_settings,
    require_manager,
    require_manager_idempotency_key,
    require_reader,
    require_sync_idempotency_key,
    require_sync_operator,
)
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.domain.tasks import resolve_duration
from app.domain.timeutil import dump_utc
from app.models import Property, Reservation, ReservationImportPreview, ReservationSyncRun
from app.services.audit import write_audit
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.instructions import (
    apply_manager_edit,
    apply_source_fields,
    latest_instruction,
    serialize_instruction,
)
from app.services.reservation_sync import (
    apply_normalized,
    create_sync_run,
    execute_sync,
    publication_blocked,
)
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["ingestion"])
_MAX_SPAN_DAYS = 93
_WEBHOOK_LIMIT = 256 * 1024


class PropertyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    external_id: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=40)
    bedrooms: int = Field(ge=0)
    duration_override_minutes: int | None = Field(default=None, gt=0)
    location: dict[str, object]
    active: bool


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    mode: str


class ImportCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_digest: str
    source_order_confirmed: bool


class InstructionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    local_overrides: dict[str, object]
    remove_override_fields: list[str]
    reason: str = Field(min_length=1, max_length=500)


class FeedCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_url: str


def _property_json(prop: Property, *, include_coordinates: bool) -> dict[str, object]:
    duration, duration_source = resolve_duration(prop.bedrooms, prop.duration_override_minutes)
    body: dict[str, object] = {
        "id": str(prop.id),
        "version": prop.version,
        "created_at": dump_utc(prop.created_at),
        "updated_at": dump_utc(prop.updated_at),
        "name": prop.name,
        "external_id": prop.external_id,
        "provider": prop.provider,
        "bedrooms": prop.bedrooms,
        "duration_override_minutes": prop.duration_override_minutes,
        "cleaning_duration_minutes": duration,
        "default_cleaning_duration_minutes": resolve_duration(prop.bedrooms, None)[0],
        "duration_source": duration_source,
        "geocode_status": prop.geocode_status,
        "active": prop.active,
    }
    if include_coordinates:
        body["latitude"] = _number(prop.latitude)
        body["longitude"] = _number(prop.longitude)
    return body


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return None


def _reservation_json(row: Reservation) -> dict[str, object]:
    return {
        "id": str(row.id),
        "version": row.version,
        "created_at": dump_utc(row.created_at),
        "updated_at": dump_utc(row.updated_at),
        "property_id": str(row.property_id),
        "provider": row.provider,
        "external_id": row.external_id,
        "status": row.status,
        "checkin_at": dump_utc(row.checkin_at),
        "checkout_at": dump_utc(row.checkout_at),
        "source_updated_at": dump_utc(row.source_updated_at) if row.source_updated_at else None,
        "timestamp_provenance": row.timestamp_provenance,
        "observed_at": dump_utc(row.observed_at),
        "source_revision": row.source_revision,
    }


def _sync_json(run: ReservationSyncRun, *, clock: Clock, settings: Settings, session: Session) -> dict[str, object]:
    return {
        "id": str(run.id),
        "provider": run.provider,
        "status": run.status,
        "started_at": dump_utc(run.started_at),
        "finished_at": dump_utc(run.finished_at) if run.finished_at else None,
        "source_as_of": dump_utc(run.source_as_of) if run.source_as_of else None,
        "snapshot_complete": run.snapshot_complete,
        "rows_seen": run.rows_seen,
        "rows_applied": run.rows_applied,
        "error_code": run.error_code,
        "cursor_before": run.cursor_before,
        "cursor_after": run.cursor_after,
        "deletion_inference": run.deletion_inference,
        "stale": publication_blocked(session, clock=clock, settings=settings),
        "publication_blocked": publication_blocked(session, clock=clock, settings=settings),
    }


@router.get("/properties")
def list_properties(
    principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    active: bool | None = None,
    limit: int = 50,
    cursor: uuid.UUID | None = None,
) -> dict[str, object]:
    if limit < 1 or limit > 100:
        raise AppError("validation_error", "limit must be from 1 to 100.", 422)
    statement = select(Property).order_by(Property.created_at, Property.id).limit(limit + 1)
    if cursor is not None:
        anchor = session.get(Property, cursor)
        if anchor is None:
            raise AppError("validation_error", "Property cursor was not found.", 422)
        statement = statement.where(tuple_(Property.created_at, Property.id) > (anchor.created_at, anchor.id))
    if active is not None:
        statement = statement.where(Property.active.is_(active))
    rows = session.scalars(statement).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    include = "viewer" not in principal.roles or "manager" in principal.roles or "owner" in principal.roles
    if principal.roles == ("viewer",):
        include = False
    return {
        "items": [_property_json(row, include_coordinates=include) for row in rows],
        "next_cursor": str(rows[-1].id) if has_more else None,
    }


@router.post("/properties")
def create_property(
    payload: PropertyWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route="/v1/properties",
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request body.", 409)
    latitude, longitude = _location(payload.location)
    now = clock.now()
    prop = Property(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider=payload.provider,
        external_id=payload.external_id,
        name=payload.name,
        timezone="America/New_York",
        bedrooms=payload.bedrooms,
        duration_override_minutes=payload.duration_override_minutes,
        location_ciphertext=b"synthetic-v1",
        latitude=latitude,
        longitude=longitude,
        geocode_status="synthetic",
        active=payload.active,
    )
    session.add(prop)
    session.flush()
    response = _property_json(prop, include_coordinates=True)
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route="/v1/properties",
        key=idempotency_key,
        digest=digest,
        status=201,
        body=response,
        resource_id=prop.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="property.created",
        entity_type="property",
        entity_id=prop.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason="synthetic property",
        redacted_changes={"external_id": payload.external_id},
        request_id=request.state.request_id,
    )
    session.commit()
    return JSONResponse(status_code=201, content=response)


@router.post("/reservation-syncs")
def start_sync(
    payload: SyncRequest,
    request: Request,
    principal: Principal = Depends(require_sync_operator),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_sync_idempotency_key),
) -> JSONResponse:
    if payload.mode not in {"incremental", "full"}:
        raise AppError("validation_error", "mode must be incremental or full.", 422)
    if settings.hospitable_read_only:
        if payload.provider != "hospitable" or payload.mode != "full":
            raise AppError(
                "unsupported_capability", "This workspace supports only full read-only Hospitable sync.", 422
            )
        from app.services.hospitable_sync import sync_hospitable

        return JSONResponse(
            content=sync_hospitable(
                session, settings=settings, clock=clock, actor=principal.subject, key=idempotency_key
            )
        )
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route="/v1/reservation-syncs",
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request body.", 409)
    run = create_sync_run(
        session,
        clock=clock,
        provider=payload.provider,
        strategy=payload.mode,
        idempotency_key=idempotency_key,
    )
    session.commit()
    execute_sync(session, clock=clock, sync_run_id=run.id)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="reservation_sync.completed",
        entity_type="reservation_sync_run",
        entity_id=run.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason=run.status,
        redacted_changes={"rows_applied": run.rows_applied, "rows_seen": run.rows_seen, "status": run.status},
        request_id=request.state.request_id,
    )
    response: dict[str, object] = {
        "job_id": str(run.id),
        "status": "queued",
        "status_url": f"/v1/reservation-syncs/{run.id}",
    }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route="/v1/reservation-syncs",
        key=idempotency_key,
        digest=digest,
        status=202,
        body=response,
        resource_id=run.id,
    )
    session.commit()
    return JSONResponse(status_code=202, content=response)


@router.get("/reservation-syncs/{sync_id}")
def get_sync(
    sync_id: uuid.UUID,
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    run = session.get(ReservationSyncRun, sync_id)
    if run is None:
        raise AppError("not_found", "Reservation sync was not found.", 404)
    return _sync_json(run, clock=clock, settings=settings, session=session)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    principal: Principal = Depends(require_sync_operator),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    run = session.get(ReservationSyncRun, job_id)
    if run is None:
        raise AppError("not_found", "Job was not found.", 404)
    state = {
        "queued": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "partial": "failed",
        "failed": "failed",
    }
    return {
        "id": str(run.id),
        "state": state[run.status],
        "result_id": str(run.id) if run.status == "succeeded" else None,
        "error": run.error_code,
        "owner": principal.subject,
    }


@router.get("/reservations")
def list_reservations(
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    property_id: uuid.UUID | None = None,
    status: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = 50,
) -> dict[str, object]:
    if from_time is None or to_time is None:
        raise AppError("validation_error", "from_time and to_time are required.", 422)
    if from_time.tzinfo is None or to_time.tzinfo is None:
        raise AppError("validation_error", "Timestamps must include an offset.", 422)
    if to_time <= from_time or (to_time - from_time) > timedelta(days=_MAX_SPAN_DAYS):
        raise AppError("validation_error", "Interval must be positive and at most 93 days.", 422)
    if limit < 1 or limit > 100:
        raise AppError("validation_error", "limit must be from 1 to 100.", 422)
    statement = (
        select(Reservation)
        .where(Reservation.checkout_at > from_time, Reservation.checkin_at < to_time)
        .order_by(Reservation.checkin_at, Reservation.id)
        .limit(limit)
    )
    if property_id is not None:
        statement = statement.where(Reservation.property_id == property_id)
    if status is not None:
        statement = statement.where(Reservation.status == status)
    rows = session.scalars(statement).all()
    return {"items": [_reservation_json(row) for row in rows], "next_cursor": None}


@router.post("/reservation-imports/preview")
async def preview_import(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise AppError("payload_too_large", "CSV upload exceeds 5 MB.", 413)
    digest = hashlib.sha256(content).hexdigest()
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route="/v1/reservation-imports/preview",
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different file.", 409)
    valid, errors = parse_reservation_csv(content)
    now = clock.now()
    preview = ReservationImportPreview(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        digest=digest,
        valid_rows=valid,
        errors=errors,
        expires_at=now + timedelta(hours=24),
        committed_at=None,
        actor_subject=principal.subject,
        status="preview",
    )
    session.add(preview)
    session.flush()
    response = {
        "import_id": str(preview.id),
        "digest": digest,
        "valid_rows": len(valid),
        "errors": errors,
        "expires_at": dump_utc(preview.expires_at),
    }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route="/v1/reservation-imports/preview",
        key=idempotency_key,
        digest=digest,
        status=201,
        body=response,
        resource_id=preview.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="reservation_import.previewed",
        entity_type="reservation_import_preview",
        entity_id=preview.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason="csv preview",
        redacted_changes={"valid_rows": len(valid), "errors": len(errors)},
        request_id=request.state.request_id,
    )
    session.commit()
    return JSONResponse(status_code=201, content=response)


@router.post("/reservation-imports/{import_id}/commit")
def commit_import(
    import_id: uuid.UUID,
    payload: ImportCommit,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route=f"/v1/reservation-imports/{import_id}/commit",
        key=idempotency_key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request body.", 409)
    preview = session.get(ReservationImportPreview, import_id)
    if preview is None:
        raise AppError("not_found", "Import preview was not found.", 404)
    if preview.digest != payload.expected_digest:
        raise AppError("digest_mismatch", "Import digest does not match the preview.", 409)
    if preview.expires_at <= clock.now():
        raise AppError("preview_expired", "Import preview has expired.", 409)
    if preview.errors:
        raise AppError("invalid_rows", "Preview errors block commit.", 422)
    if not payload.source_order_confirmed:
        raise AppError("order_unconfirmed", "Source order must be confirmed before commit.", 422)
    response: dict[str, object]
    if preview.status == "committed":
        response = {
            "job_id": str(preview.id),
            "status": "queued",
            "status_url": f"/v1/jobs/{preview.id}",
        }
    else:
        for row in preview.valid_rows:
            record = _record_from_csv(row, clock.now())
            apply_normalized(session, clock=clock, run=None, record=record)
        preview.status = "committed"
        preview.committed_at = clock.now()
        preview.version += 1
        response = {
            "job_id": str(preview.id),
            "status": "queued",
            "status_url": f"/v1/jobs/{preview.id}",
        }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route=f"/v1/reservation-imports/{import_id}/commit",
        key=idempotency_key,
        digest=digest,
        status=202,
        body=response,
        resource_id=preview.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="reservation_import.committed",
        entity_type="reservation_import_preview",
        entity_id=preview.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason="csv commit",
        redacted_changes={"rows": len(preview.valid_rows)},
        request_id=request.state.request_id,
    )
    session.commit()
    return JSONResponse(status_code=202, content=response)


@router.get("/properties/{property_id}/instructions")
def get_instructions(
    property_id: uuid.UUID,
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    instruction = latest_instruction(session, property_id)
    if instruction is None:
        raise AppError("not_found", "Instructions were not found.", 404)
    return serialize_instruction(instruction)


@router.post("/properties/{property_id}/instructions/versions")
def edit_instructions(
    property_id: uuid.UUID,
    payload: InstructionEdit,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    prop = session.get(Property, property_id)
    if prop is None:
        raise AppError("not_found", "Property was not found.", 404)
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    route = f"/v1/properties/{property_id}/instructions/versions"
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
    if payload.local_overrides:
        instruction = apply_manager_edit(
            session,
            clock=clock,
            prop=prop,
            expected_version=payload.expected_version,
            local_overrides=payload.local_overrides,
            remove_fields=[],
            reason=payload.reason,
            author_subject=principal.subject,
            key_material=settings.field_encryption_secret,
        )
        expected = instruction.instruction_version
    else:
        expected = payload.expected_version
        instruction = None
    if payload.remove_override_fields:
        instruction = apply_manager_edit(
            session,
            clock=clock,
            prop=prop,
            expected_version=expected,
            local_overrides={},
            remove_fields=payload.remove_override_fields,
            reason=payload.reason,
            author_subject=principal.subject,
            key_material=settings.field_encryption_secret,
        )
    if instruction is None:
        raise AppError("validation_error", "An instruction change is required.", 422)
    response = serialize_instruction(instruction)
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
        resource_id=instruction.id,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="property_instruction.revised",
        entity_type="property_instruction",
        entity_id=instruction.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason=payload.reason,
        redacted_changes={"instruction_version": instruction.instruction_version},
        request_id=request.state.request_id,
    )
    session.commit()
    return JSONResponse(status_code=201, content=response)


@router.post("/properties/{property_id}/instructions/source")
def import_instruction_source(
    property_id: uuid.UUID,
    payload: dict[str, object],
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    prop = session.get(Property, property_id)
    if prop is None:
        raise AppError("not_found", "Property was not found.", 404)
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise AppError("validation_error", "fields must be an object.", 422)
    typed_fields = {str(key): value for key, value in fields.items()}
    source_version = payload.get("source_version")
    instruction = apply_source_fields(
        session,
        clock=clock,
        prop=prop,
        fields=typed_fields,
        source_version=str(source_version) if source_version else None,
        author_subject=principal.subject,
        reason="source import",
        key_material=settings.field_encryption_secret,
    )
    response = serialize_instruction(instruction)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="property_instruction.imported",
        entity_type="property_instruction",
        entity_id=instruction.id,
        correlation_id=uuid.UUID(request.state.request_id),
        reason="source import",
        redacted_changes={"instruction_version": instruction.instruction_version},
        request_id=request.state.request_id,
    )
    session.commit()
    del idempotency_key
    return JSONResponse(status_code=201, content=response)


@router.post("/calendar-feeds/check")
def check_feed(payload: FeedCheck, _principal: Principal = Depends(require_manager)) -> dict[str, object]:
    try:
        assert_feed_url_allowed(payload.feed_url, allowlist=set())
    except AppError:
        raise
    except Exception as exc:
        raise AppError("feed_rejected", "Calendar feed host is not allowlisted.", 422) from exc
    return {"accepted": False, "realtime": False}


@router.post("/webhooks/hospitable")
async def hospitable_webhook(
    request: Request,
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    raw = await request.body()
    if len(raw) > _WEBHOOK_LIMIT:
        raise AppError("payload_too_large", "Webhook body exceeds 256 KB.", 413)
    signature = request.headers.get("x-mock-signature", "")
    if signature == "":
        raise AppError(
            "capability_unavailable",
            "Hospitable webhook ingress is disabled until verification.",
            403,
        )
    from app.adapters.reservations.mock import MOCK_TEST_SIGNATURE, MockReservationProvider

    if signature != MOCK_TEST_SIGNATURE:
        raise AppError("invalid_signature", "Webhook signature was rejected.", 401)
    envelope = MockReservationProvider().verify_webhook(raw, dict(request.headers), str(request.url))
    from app.models import WebhookEvent

    existing = session.scalar(
        select(WebhookEvent).where(WebhookEvent.provider == "mock", WebhookEvent.dedupe_key == envelope.dedupe_key)
    )
    if existing is not None:
        return JSONResponse(status_code=202, content={"event_id": str(existing.id), "status": "accepted"})
    now = clock.now()
    event = WebhookEvent(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider="mock",
        external_event_id=None,
        dedupe_key=envelope.dedupe_key,
        payload_digest=envelope.dedupe_key,
        payload_encrypted=None,
        verified_at=now,
        received_at=now,
        processed_at=None,
        status="received",
        attempt_count=1,
        error_code=None,
    )
    session.add(event)
    session.commit()
    _process_webhook_payload(session, clock, raw, event)
    session.commit()
    return JSONResponse(status_code=202, content={"event_id": str(event.id), "status": "accepted"})


def _process_webhook_payload(session: Session, clock: Clock, raw: bytes, event: object) -> None:
    import json

    from app.adapters.reservations.mock import MockReservationProvider
    from app.models import WebhookEvent

    if not isinstance(event, WebhookEvent):
        return
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        event.status = "quarantined"
        event.error_code = "malformed_json"
        event.processed_at = clock.now()
        return
    if not isinstance(payload, dict):
        event.status = "quarantined"
        event.error_code = "malformed_json"
        event.processed_at = clock.now()
        return
    try:
        records = MockReservationProvider().normalize_event({str(key): value for key, value in payload.items()})
    except Exception:
        event.status = "quarantined"
        event.error_code = "invalid_reservation"
        event.processed_at = clock.now()
        return
    for record in records:
        apply_normalized(session, clock=clock, run=None, record=record)
    event.status = "processed"
    event.processed_at = clock.now()


def _record_from_csv(row: dict[str, object], observed: datetime) -> NormalizedReservation:
    checkin = datetime.fromisoformat(str(row["checkin_at"]).replace("Z", "+00:00")).astimezone(UTC)
    checkout = datetime.fromisoformat(str(row["checkout_at"]).replace("Z", "+00:00")).astimezone(UTC)
    updated_raw = str(row.get("source_updated_at") or "")
    updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00")).astimezone(UTC) if updated_raw else None
    revision = str(row.get("source_revision") or "") or None
    return NormalizedReservation(
        provider=str(row["provider"]),
        external_id=str(row["external_id"]),
        property_external_id=str(row["property_external_id"]),
        status=str(row["status"]),
        checkin_at=checkin,
        checkout_at=checkout,
        source_updated_at=updated,
        source_revision=revision,
        timestamp_provenance="csv-offset",
        observed_at=observed,
    )


def _location(location: dict[str, object]) -> tuple[float | None, float | None]:
    if "address" in location:
        address = str(location["address"])
        if not address.startswith("synthetic:"):
            raise AppError("validation_error", "Only synthetic addresses can be stored in M2.", 422)
        return None, None
    if "latitude" in location and "longitude" in location:
        latitude_raw = location["latitude"]
        longitude_raw = location["longitude"]
        if isinstance(latitude_raw, bool) or isinstance(longitude_raw, bool):
            raise AppError("validation_error", "Coordinates must be numbers.", 422)
        if not isinstance(latitude_raw, int | float) or not isinstance(longitude_raw, int | float):
            raise AppError("validation_error", "Coordinates must be numbers.", 422)
        latitude = float(latitude_raw)
        longitude = float(longitude_raw)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise AppError("validation_error", "Coordinates are out of range.", 422)
        return latitude, longitude
    raise AppError("validation_error", "Location must be a synthetic address or coordinates.", 422)
