from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime

import redis
from app.api.deps import (
    get_clock,
    get_db,
    get_redis,
    get_settings,
    require_manager,
    require_manager_idempotency_key,
    require_reader,
    require_sync_idempotency_key,
    require_sync_operator,
)
from app.api.languages import PreferredLanguage, ReadingLanguages
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.domain.timeutil import dump_utc
from app.models import (
    Assignment,
    Cleaner,
    CleanerAccessLink,
    CleanerAvailability,
    CleanerEligibility,
    Property,
    ScheduleDay,
    ScheduleRun,
)
from app.services.audit import write_audit
from app.services.eligibility import eligible_properties
from app.services.email_addresses import normalize_email
from app.services.field_cipher import protect_text, reveal_text
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.phone_numbers import normalize_phone
from app.services.rate_limit import enforce_rate_limit
from app.services.schedule_freshness import _bump
from app.services.schedules import (
    gather_preview_inputs,
    lock_assignment,
    mark_unavailable,
    override_assignment,
    publish_schedule,
    run_preview_solver,
    store_preview_run,
)
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["schedules"])

_ORIGIN = re.compile(r"synthetic:[a-z0-9-]{1,48}")


class CleanerWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=7, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    max_jobs: int | None = Field(default=None, gt=0, le=20)
    max_service_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    max_duty_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    eligibility_property_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    excluded_property_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    reading_languages: ReadingLanguages = Field(default_factory=list)
    preferred_language: PreferredLanguage | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cleaner name must not be blank.")
        return value.strip()

    @field_validator("eligibility_property_ids", "excluded_property_ids")
    @classmethod
    def _eligible(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Each eligible property can appear only once.")
        return value

    @model_validator(mode="after")
    def _eligibility(self) -> CleanerWrite:
        if self.eligibility_property_ids is not None and self.excluded_property_ids:
            raise ValueError("Use an explicit allowlist or exclusions, not both.")
        return self


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime
    end_at: datetime


class AvailabilityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_date: date
    status: str
    work_intervals: list[Interval] = Field(default_factory=list)
    unavailable_intervals: list[Interval] = Field(default_factory=list)
    origin_label: str | None = None
    max_jobs_override: int | None = Field(default=None, gt=0)
    max_service_override: int | None = Field(default=None, gt=0)
    max_duty_override: int | None = Field(default=None, gt=0)
    expected_version: int | None = None


class PreviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_date: date


class PublishWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fingerprint: str = Field(min_length=64, max_length=64)
    expected_day_revision: int = Field(gt=0)


class LockWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool
    reason: str = Field(min_length=1, max_length=500)


class OverrideWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleaner_id: uuid.UUID
    planned_start: datetime
    is_locked: bool
    reason: str = Field(min_length=1, max_length=500)


@router.get("/cleaners")
def list_cleaners(
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    rows = session.scalars(select(Cleaner).where(Cleaner.active.is_(True)).order_by(Cleaner.display_name)).all()
    return {"items": [_cleaner_json(row, session) for row in rows]}


@router.post("/cleaners")
def post_cleaner(
    payload: CleanerWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    body = payload.model_dump(mode="json")
    if "preferred_language" not in payload.model_fields_set:
        body.pop("preferred_language")
    if "reading_languages" not in payload.model_fields_set:
        body.pop("reading_languages")
    if payload.eligibility_property_ids is not None:
        body.pop("excluded_property_ids")
    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        idempotency_key,
        "/v1/cleaners",
        body,
        lambda: _create_cleaner(session, clock, settings, payload, principal.subject),
        201,
    )


class CleanerLanguagesWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)
    reading_languages: ReadingLanguages
    preferred_language: PreferredLanguage | None = None


@router.post("/cleaners/{cleaner_id}/languages")
def save_cleaner_languages(
    cleaner_id: uuid.UUID,
    payload: CleanerLanguagesWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        cleaner = session.scalar(
            select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update().execution_options(populate_existing=True)
        )
        if cleaner is None or not cleaner.active:
            raise AppError("not_found", "Active cleaner was not found.", 404)
        if cleaner.version != payload.expected_version:
            raise AppError("stale_version", "Cleaner changed. Reload their details.", 409)
        if cleaner.reading_languages != payload.reading_languages or (
            "preferred_language" in payload.model_fields_set
            and cleaner.preferred_language != payload.preferred_language
        ):
            if "preferred_language" in payload.model_fields_set:
                cleaner.preferred_language = payload.preferred_language
            cleaner.reading_languages = list(payload.reading_languages)
            cleaner.version += 1
            cleaner.updated_at = clock.now()
            write_audit(
                session,
                clock=clock,
                actor_type="operator",
                actor_subject=principal.subject,
                action="cleaner.reading_languages_updated",
                entity_type="cleaner",
                entity_id=cleaner.id,
                correlation_id=uuid.UUID(request.state.request_id),
                reason="Manager updated cleaner reading languages.",
                redacted_changes={"language_count": len(payload.reading_languages)},
                request_id=request.state.request_id,
            )
        session.flush()
        return {
            "id": str(cleaner.id),
            "version": cleaner.version,
            "reading_languages": cleaner.reading_languages,
            "preferred_language": cleaner.preferred_language,
        }

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/languages",
        payload.model_dump(mode="json", exclude={"preferred_language"} - payload.model_fields_set),
        action,
        200,
    )


class CleanerEmailWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)
    email: str | None = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return normalize_email(value)


@router.get("/cleaners/{cleaner_id}/email")
def get_cleaner_email(
    cleaner_id: uuid.UUID,
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("not_found", "Active cleaner was not found.", 404)
    return {
        "version": cleaner.version,
        "email_stored": cleaner.email_ciphertext is not None,
        "email": reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
        if cleaner.email_ciphertext is not None
        else None,
    }


@router.post("/cleaners/{cleaner_id}/email")
def save_cleaner_email(
    cleaner_id: uuid.UUID,
    payload: CleanerEmailWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        cleaner = session.scalar(select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update())
        if cleaner is None or not cleaner.active:
            raise AppError("not_found", "Active cleaner was not found.", 404)
        if cleaner.version != payload.expected_version:
            raise AppError("stale_version", "Cleaner changed. Reload their contact details.", 409)
        old_email = (
            reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
            if cleaner.email_ciphertext
            else None
        )
        if old_email != payload.email:
            for grant in session.scalars(
                select(CleanerAccessLink).where(
                    CleanerAccessLink.cleaner_id == cleaner.id,
                    CleanerAccessLink.purpose == "jobs",
                    CleanerAccessLink.revoked_at.is_(None),
                )
            ):
                grant.revoked_at = clock.now()
        cleaner.email_ciphertext = (
            protect_text(payload.email, settings.field_encryption_secret) if payload.email else None
        )
        cleaner.version += 1
        cleaner.updated_at = clock.now()
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="cleaner.email_updated",
            entity_type="cleaner",
            entity_id=cleaner_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Manager updated cleaner email.",
            redacted_changes={"email_stored": cleaner.email_ciphertext is not None},
            request_id=request.state.request_id,
        )
        session.flush()
        return {
            "cleaner_id": str(cleaner.id),
            "version": cleaner.version,
            "email_stored": cleaner.email_ciphertext is not None,
        }

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/email",
        payload.model_dump(mode="json"),
        action,
        200,
    )


class EligibilityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)
    excluded_property_ids: list[uuid.UUID] = Field(max_length=100)

    @field_validator("excluded_property_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Each excluded house can appear only once.")
        return value


@router.get("/cleaners/{cleaner_id}/eligibility")
def get_eligibility(
    cleaner_id: uuid.UUID,
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None or not cleaner.active:
        raise AppError("not_found", "Active cleaner was not found.", 404)
    active = set(session.scalars(select(Property.id).where(Property.active.is_(True))))
    return {
        "version": cleaner.version,
        "eligibility_policy": cleaner.eligibility_policy,
        "excluded_property_ids": sorted(str(identity) for identity in active - eligible_properties(session, cleaner)),
    }


@router.post("/cleaners/{cleaner_id}/eligibility")
def save_eligibility(
    cleaner_id: uuid.UUID,
    payload: EligibilityWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    def action() -> dict[str, object]:
        cleaner = session.scalar(select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update())
        if cleaner is None or not cleaner.active:
            raise AppError("not_found", "Active cleaner was not found.", 404)
        if cleaner.version != payload.expected_version:
            raise AppError("stale_version", "Cleaner changed. Reload their house exceptions.", 409)
        active = set(session.scalars(select(Property.id).where(Property.active.is_(True))))
        excluded = set(payload.excluded_property_ids)
        if not excluded <= active:
            raise AppError("validation_error", "Choose active houses for exclusions.", 422)
        before = eligible_properties(session, cleaner)
        session.execute(
            delete(CleanerEligibility).where(
                CleanerEligibility.cleaner_id == cleaner_id, CleanerEligibility.property_id.in_(active)
            )
        )
        now = clock.now()
        for identity in excluded:
            session.add(
                CleanerEligibility(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    cleaner_id=cleaner_id,
                    property_id=identity,
                    eligible=False,
                    reason="Manager excluded house",
                )
            )
        cleaner.eligibility_policy = "all_except_exclusions"
        cleaner.version += 1
        cleaner.updated_at = now
        for day in session.scalars(select(ScheduleDay).with_for_update()):
            day.revision += 1
            day.updated_at = now
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="cleaner.eligibility_updated",
            entity_type="cleaner",
            entity_id=cleaner_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Manager confirmed house exceptions.",
            redacted_changes={
                "previous_eligible": sorted(str(identity) for identity in before),
                "excluded": sorted(str(identity) for identity in excluded),
            },
            request_id=request.state.request_id,
        )
        session.flush()
        return {
            "version": cleaner.version,
            "eligibility_policy": cleaner.eligibility_policy,
            "excluded_property_ids": sorted(str(identity) for identity in excluded),
        }

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/eligibility",
        payload.model_dump(mode="json"),
        action,
        200,
    )


class CleanerRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)


@router.post("/cleaners/{cleaner_id}/remove")
def post_remove_cleaner(
    cleaner_id: uuid.UUID,
    payload: CleanerRemoval,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    from app.services.cleaner_removal import remove_cleaner

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/remove",
        payload.model_dump(mode="json"),
        lambda: remove_cleaner(
            session,
            clock=clock,
            cleaner_id=cleaner_id,
            expected_version=payload.expected_version,
            actor=principal.subject,
        ),
        200,
    )


@router.post("/cleaners/{cleaner_id}/availability")
def post_availability(
    cleaner_id: uuid.UUID,
    payload: AvailabilityWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    route = f"/v1/cleaners/{cleaner_id}/availability"
    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        idempotency_key,
        route,
        payload.model_dump(mode="json"),
        lambda: _save_availability(session, clock, settings, cleaner_id, payload, principal.subject),
        200,
    )


@router.post("/schedules/previews")
def post_preview(
    payload: PreviewWrite,
    request: Request,
    principal: Principal = Depends(require_sync_operator),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
    idempotency_key: str = Depends(require_sync_idempotency_key),
) -> JSONResponse:
    enforce_rate_limit(
        cache,
        clock=clock,
        subject=principal.subject,
        bucket="preview",
        limit=settings.preview_rate_limit_per_minute,
    )
    route = "/v1/schedules/previews"
    body = payload.model_dump(mode="json")
    digest = request_digest(body)
    if settings.hospitable_read_only or principal.has_role("cleaner"):
        digest = hmac.new(settings.field_encryption_secret.encode(), digest.encode(), hashlib.sha256).hexdigest()
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
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
    inputs = gather_preview_inputs(
        session, clock=clock, settings=settings, service_date=payload.service_date, actor=principal.subject
    )
    existing = session.scalar(
        select(ScheduleRun).where(
            ScheduleRun.service_date == payload.service_date,
            ScheduleRun.input_fingerprint == inputs.fingerprint,
            ScheduleRun.day_revision == inputs.day_revision,
            ScheduleRun.status == "preview",
        )
    )
    if existing is not None:
        response = _run_json(existing)
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
            resource_id=None,
        )
        session.commit()
        return JSONResponse(status_code=200, content=response)
    session.commit()
    result, started, finished = run_preview_solver(inputs, clock)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    run = store_preview_run(
        session, clock=clock, settings=settings, inputs=inputs, result=result, started=started, finished=finished
    )
    response = _run_json(run)
    try:
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
            resource_id=None,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        resolution = resolve_idempotency(
            session,
            principal_subject=principal.subject,
            route=route,
            key=idempotency_key,
            digest=digest,
            clock=clock,
        )
        if resolution.outcome == "replay" and resolution.body is not None:
            return JSONResponse(status_code=resolution.status or 200, content=resolution.body)
        raise AppError("idempotency_conflict", "Concurrent request with same idempotency key.", 409) from None
    return JSONResponse(status_code=200, content=response)


@router.get("/schedules/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    run = session.get(ScheduleRun, run_id)
    if run is None:
        raise AppError("not_found", "Schedule run was not found.", 404)
    return _run_json(run)


@router.get("/schedules")
def get_schedule(
    service_date: date,
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == service_date))
    active = None if day is None or day.active_run_id is None else session.get(ScheduleRun, day.active_run_id)
    return {
        "service_date": service_date.isoformat(),
        "revision": None if day is None else day.revision,
        "active_run": None if active is None else _run_json(active),
    }


@router.post("/schedules/runs/{run_id}/publish")
def post_publish(
    run_id: uuid.UUID,
    payload: PublishWrite,
    request: Request,
    principal: Principal = Depends(require_sync_operator),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_sync_idempotency_key),
) -> JSONResponse:
    expected = _if_match(request)
    route = f"/v1/schedules/runs/{run_id}/publish"
    body = payload.model_dump(mode="json")
    body["if_match"] = expected
    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        idempotency_key,
        route,
        body,
        lambda: publish_schedule(
            session,
            clock=clock,
            settings=settings,
            run_id=run_id,
            expected_fingerprint=payload.expected_fingerprint,
            expected_day_revision=payload.expected_day_revision,
            expected_version=expected,
            actor=principal.subject,
        ),
        200,
    )


@router.post("/assignments/{assignment_id}/lock")
def post_lock(
    assignment_id: uuid.UUID,
    payload: LockWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    expected = _if_match(request)
    route = f"/v1/assignments/{assignment_id}/lock"
    body = payload.model_dump(mode="json")
    body["if_match"] = expected
    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        idempotency_key,
        route,
        body,
        lambda: _lock_body(session, clock, assignment_id, expected, payload, principal.subject),
        200,
    )


@router.post("/assignments/{assignment_id}/override")
def post_override(
    assignment_id: uuid.UUID,
    payload: OverrideWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    idempotency_key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    if payload.is_locked:
        raise AppError("validation_error", "Lock changes use the lock endpoint.", 422)
    expected = _if_match(request)
    route = f"/v1/assignments/{assignment_id}/override"
    body = payload.model_dump(mode="json")
    body["if_match"] = expected
    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        idempotency_key,
        route,
        body,
        lambda: _override_body(session, clock, assignment_id, expected, payload, principal.subject),
        200,
    )


def _create_cleaner(
    session: Session,
    clock: Clock,
    settings: Settings,
    payload: CleanerWrite,
    actor: str,
) -> dict[str, object]:
    phone = normalize_phone(payload.phone)
    digest = hmac.new(settings.field_encryption_secret.encode(), phone.encode(), hashlib.sha256).hexdigest()
    if session.scalar(select(Cleaner).where(Cleaner.phone_lookup_hmac == digest)) is not None:
        raise AppError("duplicate_phone", "Cleaner phone is already registered.", 409)
    now = clock.now()
    row = Cleaner(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        display_name=payload.display_name,
        phone_ciphertext=protect_text(phone, settings.field_encryption_secret),
        email_ciphertext=protect_text(payload.email, settings.field_encryption_secret) if payload.email else None,
        reading_languages=list(payload.reading_languages),
        preferred_language=payload.preferred_language,
        phone_lookup_hmac=digest,
        active=True,
        sms_consent_status="pending",
        max_jobs=payload.max_jobs,
        max_service_minutes=payload.max_service_minutes,
        max_duty_minutes=payload.max_duty_minutes,
        default_work_intervals=[],
        eligibility_policy="all_except_exclusions" if payload.eligibility_property_ids is None else "allowlist",
    )
    session.add(row)
    session.flush()
    property_ids = (
        payload.excluded_property_ids if payload.eligibility_property_ids is None else payload.eligibility_property_ids
    )
    for property_id in property_ids:
        prop = session.get(Property, property_id)
        if prop is None or not prop.active:
            raise AppError("not_found", "Eligibility property was not found.", 404)
        session.add(
            CleanerEligibility(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                cleaner_id=row.id,
                property_id=property_id,
                eligible=payload.eligibility_property_ids is not None,
                reason="Manager-confirmed eligibility",
            )
        )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="cleaner.created",
        entity_type="cleaner",
        entity_id=row.id,
        correlation_id=row.id,
        reason="Manager added cleaner",
        redacted_changes={
            "phone_stored": True,
            "email_stored": row.email_ciphertext is not None,
            "eligibility_policy": row.eligibility_policy,
            "property_rules": len(property_ids),
        },
        request_id=None,
    )
    return _cleaner_json(row)


def _save_availability(
    session: Session,
    clock: Clock,
    settings: Settings,
    cleaner_id: uuid.UUID,
    payload: AvailabilityWrite,
    actor: str,
) -> dict[str, object]:
    workspace_id = get_workspace_id(session)
    acquire_workspace_lock(session, workspace_id, SCHEDULE_PUBLICATION_LOCK)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{workspace_id}:availability:{cleaner_id}:{payload.service_date}"},
    )
    if payload.status not in {"available", "unavailable", "partial"}:
        raise AppError("validation_error", "Availability status is invalid.", 422)
    if session.get(Cleaner, cleaner_id) is None:
        raise AppError("not_found", "Cleaner was not found.", 404)
    _validate_intervals(payload)
    existing = session.scalar(
        select(CleanerAvailability).where(
            CleanerAvailability.cleaner_id == cleaner_id,
            CleanerAvailability.service_date == payload.service_date,
        )
    )
    if payload.status == "unavailable" and existing is None:
        from app.services.weekly_availability import effective_availability

        weekly = next(
            (row for row in effective_availability(session, payload.service_date) if row.cleaner_id == cleaner_id), None
        )
        if weekly is not None:
            if payload.expected_version not in {None, 0}:
                raise AppError("stale_version", "Availability version is stale.", 409)
            result = mark_unavailable(
                session,
                clock=clock,
                settings=settings,
                cleaner_id=cleaner_id,
                service_date=payload.service_date,
                expected_version=weekly.version,
                actor=actor,
            )
            return result
    if payload.status == "unavailable" and existing is not None:
        if payload.expected_version is None:
            raise AppError("validation_error", "expected_version is required.", 422)
        result = mark_unavailable(
            session,
            clock=clock,
            settings=settings,
            cleaner_id=cleaner_id,
            service_date=payload.service_date,
            expected_version=payload.expected_version,
            actor=actor,
        )
        result["availability_version"] = existing.version
        return result
    if existing is None:
        if payload.expected_version not in {None, 0}:
            raise AppError("stale_version", "Availability version is stale.", 409)
        now = clock.now()
        row = CleanerAvailability(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=cleaner_id,
            service_date=payload.service_date,
            status=payload.status,
            work_intervals=[item.model_dump(mode="json") for item in payload.work_intervals],
            unavailable_intervals=[item.model_dump(mode="json") for item in payload.unavailable_intervals],
            origin_label=payload.origin_label,
            origin_ciphertext=_origin_ciphertext(payload.origin_label, settings),
            origin_source="explicit" if payload.origin_label else None,
            max_jobs_override=payload.max_jobs_override,
            max_service_override=payload.max_service_override,
            max_duty_override=payload.max_duty_override,
            assumed=False,
        )
        session.add(row)
        session.flush()
        _bump(session, payload.service_date, now)
        return {"id": str(row.id), "version": row.version, "status": row.status, "sms_sent": False}
    if payload.expected_version != existing.version:
        raise AppError("stale_version", "Availability version is stale.", 409)
    existing.status = payload.status
    existing.work_intervals = [item.model_dump(mode="json") for item in payload.work_intervals]
    existing.unavailable_intervals = [item.model_dump(mode="json") for item in payload.unavailable_intervals]
    existing.origin_label = payload.origin_label
    existing.origin_ciphertext = _origin_ciphertext(payload.origin_label, settings)
    existing.origin_source = "explicit" if payload.origin_label else None
    existing.max_jobs_override = payload.max_jobs_override
    existing.max_service_override = payload.max_service_override
    existing.max_duty_override = payload.max_duty_override
    existing.version += 1
    existing.updated_at = clock.now()
    _bump(session, payload.service_date, clock.now())
    session.flush()
    return {"id": str(existing.id), "version": existing.version, "status": existing.status, "sms_sent": False}


def _lock_body(
    session: Session,
    clock: Clock,
    assignment_id: uuid.UUID,
    expected: int,
    payload: LockWrite,
    actor: str,
) -> dict[str, object]:
    row = lock_assignment(
        session,
        clock=clock,
        assignment_id=assignment_id,
        expected_version=expected,
        locked=payload.locked,
        reason=payload.reason,
        actor=actor,
    )
    return _assignment_json(row)


def _override_body(
    session: Session,
    clock: Clock,
    assignment_id: uuid.UUID,
    expected: int,
    payload: OverrideWrite,
    actor: str,
) -> dict[str, object]:
    row = override_assignment(
        session,
        clock=clock,
        assignment_id=assignment_id,
        expected_version=expected,
        cleaner_id=payload.cleaner_id,
        planned_start=payload.planned_start,
        reason=payload.reason,
        actor=actor,
    )
    return {"assignment": _assignment_json(row), "sms_sent": False}


def _origin_ciphertext(origin: str | None, settings: Settings) -> bytes | None:
    if origin is None:
        return None
    return protect_text(origin, settings.field_encryption_secret)


def _validate_intervals(payload: AvailabilityWrite) -> None:
    if payload.status == "available" and (not payload.work_intervals or payload.origin_label is None):
        raise AppError("validation_error", "Available cleaners need a dated shift and origin.", 422)
    if payload.origin_label is not None and _ORIGIN.fullmatch(payload.origin_label) is None:
        raise AppError("validation_error", "Origin must be a synthetic: routing key.", 422)
    for item in [*payload.work_intervals, *payload.unavailable_intervals]:
        if item.start_at.tzinfo is None or item.end_at.tzinfo is None or item.end_at <= item.start_at:
            raise AppError("validation_error", "Intervals must be aware and ordered.", 422)


def _idempotent(
    request: Request,
    principal: Principal,
    session: Session,
    settings: Settings,
    clock: Clock,
    key: str,
    route: str,
    body: dict[str, object],
    action: Callable[[], dict[str, object]],
    status: int,
) -> JSONResponse:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    digest = request_digest(body)
    if settings.hospitable_read_only or principal.has_role("cleaner"):
        digest = hmac.new(settings.field_encryption_secret.encode(), digest.encode(), hashlib.sha256).hexdigest()
    resolution = resolve_idempotency(
        session,
        principal_subject=principal.subject,
        route=route,
        key=key,
        digest=digest,
        clock=clock,
    )
    if resolution.outcome == "replay" and resolution.body is not None and resolution.status is not None:
        return JSONResponse(status_code=resolution.status, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused with a different request body.", 409)
    response = action()
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route=route,
        key=key,
        digest=digest,
        status=status,
        body=response,
        resource_id=None,
    )
    session.commit()
    return JSONResponse(status_code=status, content=response)


def _cleaner_json(row: Cleaner, session: Session | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(row.id),
        "version": row.version,
        "display_name": row.display_name,
        "reading_languages": row.reading_languages,
        "preferred_language": row.preferred_language,
        "active": row.active,
        "sms_consent_status": row.sms_consent_status,
        "max_jobs": row.max_jobs,
        "max_service_minutes": row.max_service_minutes,
        "max_duty_minutes": row.max_duty_minutes,
        "phone_stored": bool(row.phone_ciphertext),
        "email_stored": row.email_ciphertext is not None,
        "eligibility_policy": row.eligibility_policy,
    }
    if session is not None:
        result["eligible_property_ids"] = sorted(str(pid) for pid in eligible_properties(session, row))
    return result


def _run_json(run: ScheduleRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "version": run.version,
        "service_date": run.service_date.isoformat(),
        "status": run.status,
        "input_fingerprint": run.input_fingerprint,
        "day_revision": run.day_revision,
        "solver_status": run.solver_status,
        "solver_version": run.solver_version,
        "objective_values": run.objective_values,
        "candidate_plan": run.candidate_plan,
        "uncovered": run.uncovered,
        "sms_sent": False,
        "created_at": dump_utc(run.created_at),
    }


def _assignment_json(row: Assignment) -> dict[str, object]:
    return {
        "id": str(row.id),
        "version": row.version,
        "task_id": str(row.task_id),
        "cleaner_id": str(row.cleaner_id),
        "status": row.status,
        "is_locked": row.is_locked,
        "planned_start": dump_utc(row.planned_start),
        "planned_end": dump_utc(row.planned_end),
        "acknowledged_at": dump_utc(row.acknowledged_at) if row.acknowledged_at else None,
        "assignment_version": row.assignment_version,
    }


def _if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None or raw.strip() == "":
        raise AppError("if_match_required", "If-Match is required.", 428)
    value = raw.strip().strip('"')
    if not value.isdigit() or int(value) < 1:
        raise AppError("if_match_invalid", "If-Match must be a positive integer version.", 428)
    return int(value)
