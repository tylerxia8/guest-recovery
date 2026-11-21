from __future__ import annotations

import smtplib
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from app.api.deps import get_clock, get_db, get_settings, require_idempotency_key, require_owner
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import (
    Assignment,
    Cleaner,
    CleanerScheduleVersion,
    CleaningTask,
    IdempotencyRecord,
    ScheduleRun,
)
from app.services.assignment_email import assignment_preview
from app.services.audit import write_audit
from app.services.cleaner_coordination import cleaner_portal_url, issue_portal_link
from app.services.cleaner_language import local_date, localized_email
from app.services.field_cipher import reveal_text
from app.services.gmail import read_credentials, send_email
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/email", tags=["email"])


class AssignmentSend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_date: date
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SampleAssignmentSend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/status")
def email_status(
    _principal: Principal = Depends(require_owner),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    configured = False
    if settings.gmail_credentials_file:
        try:
            read_credentials(settings.gmail_credentials_file)
            configured = True
        except (OSError, ValueError):
            pass
    return {
        "configured": configured,
        "self_test_enabled": configured and settings.gmail_test_send_enabled,
        "automatic_assignments_enabled": configured and settings.local_email_automation_enabled,
        "manual_assignments_enabled": configured
        and settings.gmail_assignment_send_enabled
        and not settings.local_email_automation_enabled,
    }


@router.get("/assignments")
def assignment_email_list(
    service_date: date | None = None,
    _principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    today = clock.now().astimezone(ZoneInfo("America/New_York")).date()
    day = service_date or today
    rows = session.execute(
        select(Cleaner, Assignment)
        .join(Assignment, Assignment.cleaner_id == Cleaner.id)
        .join(CleanerScheduleVersion, CleanerScheduleVersion.id == Assignment.cleaner_schedule_version_id)
        .join(ScheduleRun, ScheduleRun.id == Assignment.schedule_run_id)
        .join(CleaningTask, CleaningTask.id == Assignment.task_id)
        .where(
            Cleaner.active.is_(True),
            Assignment.service_date == day,
            Assignment.status.in_(("published", "acknowledged")),
            Assignment.planned_start > clock.now(),
            CleanerScheduleVersion.status == "current",
            CleanerScheduleVersion.service_date == day,
            CleanerScheduleVersion.cleaner_id == Cleaner.id,
            CleanerScheduleVersion.schedule_run_id == Assignment.schedule_run_id,
            ScheduleRun.status == "published",
            CleaningTask.status == "assigned",
        )
        .order_by(Cleaner.display_name, Assignment.planned_start)
    ).all()
    grouped: dict[str, dict[str, object]] = {}
    for cleaner, _assignment in rows:
        identity = str(cleaner.id)
        if identity not in grouped:
            grouped[identity] = {
                "cleaner_id": identity,
                "cleaner_name": cleaner.display_name,
                "email_stored": cleaner.email_ciphertext is not None,
                "assignment_count": 0,
            }
        grouped[identity]["assignment_count"] = cast(int, grouped[identity]["assignment_count"]) + 1
    return {"business_date": today.isoformat(), "service_date": day.isoformat(), "items": list(grouped.values())}


@router.get("/assignments/{cleaner_id}")
def assignment_email_preview(
    cleaner_id: uuid.UUID,
    service_date: date,
    _principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    return assignment_preview(session, settings, clock, cleaner_id, service_date)


@router.post("/assignments/{cleaner_id}")
def assignment_email_send(
    cleaner_id: uuid.UUID,
    payload: AssignmentSend,
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    if settings.local_email_automation_enabled:
        raise AppError("automatic_email_enabled", "Assignment emails are managed automatically.", 409)
    if not settings.gmail_assignment_send_enabled:
        raise AppError("assignment_email_disabled", "Manual assignment emails are disabled.", 403)

    def prepare() -> tuple[str, str, str]:
        preview = assignment_preview(session, settings, clock, cleaner_id, payload.service_date)
        if preview["fingerprint"] != payload.fingerprint:
            raise AppError("email_preview_changed", "The schedule or contact changed. Refresh the preview.", 409)
        return str(preview["recipient"]), str(preview["subject"]), str(preview["body"])

    return _deliver(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/email/assignments/{cleaner_id}",
        request_digest(payload.model_dump(mode="json")),
        prepare,
    )


def _sample_assignment(session: Session, settings: Settings, clock: Clock) -> dict[str, object]:
    cleaners = list(
        session.scalars(
            select(Cleaner)
            .where(Cleaner.active.is_(True), Cleaner.display_name == "Benito Johnson (Demo)")
            .with_for_update()
        )
    )
    if len(cleaners) != 1:
        raise AppError(
            "sample_cleaner_missing", "Choose one unique Benito Johnson (Demo) profile before previewing.", 409
        )
    cleaner = cleaners[0]
    if cleaner.email_ciphertext is None:
        raise AppError("sample_email_missing", "Save an email for Benito Johnson (Demo) first.", 409)
    recipient = reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
    day = clock.now().astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=1)
    subject = "SAMPLE ONLY — Turnover cleaning assignment"
    body = (
        "SAMPLE ONLY — NOT REAL WORK\n\n"
        "Hi Benito,\n\n"
        "This is a sample cleaning assignment to preview your Turnover emails.\n\n"
        "House: Sample apartment\n"
        "Address: 123 Example Lane (fictional address)\n"
        f"Date: {day.strftime('%A, %B %d, %Y')}\n"
        "Cleaning window: 11:00 AM–1:00 PM Eastern\n"
        "Estimated cleaning time: 2 hours\n\n"
        "This is a demonstration. Do not travel to this address.\n"
        "No real assignment has been created.\n"
    )
    subject, body = localized_email(cleaner, "sample", subject, body, {"day": local_date(day)})
    preview: dict[str, object] = {
        "cleaner_id": str(cleaner.id),
        "cleaner_version": cleaner.version,
        "recipient": recipient,
        "subject": subject,
        "body": body,
    }
    return {**preview, "fingerprint": request_digest(preview)}


@router.get("/sample-assignment")
def sampleassignment_preview(
    _principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    return _sample_assignment(session, settings, clock)


@router.post("/sample-assignment")
def sample_assignment_send(
    payload: SampleAssignmentSend,
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    if not settings.gmail_test_send_enabled:
        raise AppError("email_test_disabled", "Sample emails are disabled.", 403)

    def prepare() -> tuple[str, str, str]:
        preview = _sample_assignment(session, settings, clock)
        if preview["fingerprint"] != payload.fingerprint:
            raise AppError(
                "email_preview_changed", "The sample email changed. Refresh its preview before sending.", 409
            )
        return str(preview["recipient"]), str(preview["subject"]), str(preview["body"])

    return _deliver(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/email/sample-assignment",
        request_digest(payload.model_dump()),
        prepare,
    )


@router.post("/portal/{cleaner_id}")
def send_portal_link(
    cleaner_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    if not settings.gmail_assignment_send_enabled:
        raise AppError("email_disabled", "Cleaner email is disabled.", 403)

    def prepare() -> tuple[str, str, str]:
        cleaner = session.get(Cleaner, cleaner_id)
        if cleaner is None or not cleaner.active or not cleaner.email_ciphertext:
            raise AppError("cleaner_email_missing", "Save an email for this active cleaner first.", 409)
        issue_portal_link(session, settings, clock, cleaner.id)
        url = cleaner_portal_url(session, settings, clock, cleaner.id)
        if url is None:
            raise RuntimeError("A cleaner portal link could not be issued.")
        local = settings.cleaner_portal_base_url.startswith(("http://localhost", "http://127.0.0.1"))
        body = f"Hi {cleaner.display_name},\n\nYour private cleaner portal: {url}\n\n"
        body += "View your houses, accept work, mark cleaning done, or release a house you cannot clean.\n"
        body += "Available houses can be claimed if they fit your hours and driving time. Keep this link private.\n"
        if local:
            body += "\nThis demo link opens on the computer running Turnover. Public hosting is not enabled yet.\n"
        subject, body = localized_email(
            cleaner,
            "portal_welcome",
            "Your Turnover cleaner portal",
            body,
            {"url": url, "local": "yes" if local else ""},
        )
        return reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret), subject, body

    return _deliver(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/email/portal/{cleaner_id}",
        request_digest({"cleaner_id": str(cleaner_id), "template": "portal-v1"}),
        prepare,
    )


@router.post("/test")
def email_test(
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_idempotency_key),
) -> JSONResponse:
    if not settings.gmail_test_send_enabled:
        raise AppError("email_test_disabled", "Connection test emails are disabled.", 403)

    def prepare() -> tuple[str, str, str]:
        address, _ = read_credentials(settings.gmail_credentials_file)
        return (
            address,
            "Turnover email connection",
            "Your Turnover Gmail connection works. No cleaner assignments were sent.",
        )

    return _deliver(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        "/v1/email/test",
        request_digest({"template": "self-test-v1"}),
        prepare,
    )


def _deliver(
    request: Request,
    principal: Principal,
    session: Session,
    settings: Settings,
    clock: Clock,
    key: str,
    route: str,
    digest: str,
    prepare: Callable[[], tuple[str, str, str]],
) -> JSONResponse:
    if not settings.hospitable_read_only:
        raise AppError("email_test_disabled", "Configure Gmail locally before sending a sample email.", 403)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('gmail-manual-send-v1'))"))
    resolution = resolve_idempotency(
        session, principal_subject=principal.subject, route=route, key=key, digest=digest, clock=clock
    )
    if resolution.outcome == "replay":
        return JSONResponse(status_code=resolution.status or 202, content=resolution.body)
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "This email request is already recorded with different details.", 409)
    try:
        read_credentials(settings.gmail_credentials_file)
        recipient, subject, content = prepare()
    except (OSError, ValueError):
        raise AppError(
            "email_not_configured", "The private Gmail credentials file is missing or invalid.", 409
        ) from None
    correlation = uuid.uuid4()
    body: dict[str, object] = {
        "status": "delivery_unknown",
        "message": "Sending started. Check the recipient inbox before requesting another email.",
    }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal.subject,
        route=route,
        key=key,
        digest=digest,
        status=202,
        body=body,
        resource_id=correlation,
    )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="email_manual_send_started",
        entity_type="email",
        entity_id=correlation,
        correlation_id=correlation,
        reason=None,
        redacted_changes={"route": route},
        request_id=request.state.request_id,
    )
    session.commit()
    response_status = 200
    try:
        session.connection()
        recipient, subject, content = prepare()
        send_email(settings.gmail_credentials_file, str(correlation), recipient, subject, content)
        body = {"status": "accepted", "message": "Gmail accepted the email. Check the recipient inbox."}
    except AppError as error:
        response_status = error.status_code
        body = {
            "status": "preview_changed",
            "message": error.message,
            "error": {"code": error.code, "message": error.message},
        }
    except smtplib.SMTPAuthenticationError:
        body = {"status": "authentication_failed", "message": "Gmail rejected the login. Check the app password."}
    except (OSError, ValueError, smtplib.SMTPException):
        body = {
            "status": "delivery_unknown",
            "message": "Delivery could not be confirmed. Check the recipient inbox before retrying.",
        }
    record = session.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.resource_id == correlation).with_for_update()
    )
    if record is None:
        raise RuntimeError("Email send reservation is missing.")
    record.response_body = body
    record.response_status = response_status
    record.updated_at = clock.now()
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="email_manual_send_finished",
        entity_type="email",
        entity_id=correlation,
        correlation_id=correlation,
        reason=None,
        redacted_changes={"status": body["status"], "route": route},
        request_id=request.state.request_id,
    )
    session.commit()
    return JSONResponse(status_code=response_status, content=body)
