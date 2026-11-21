# ruff: noqa: B008
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.adapters.types import VerifiedSmsEvent
from app.api.app_factory import create_app
from app.api.deps import get_clock, get_db, require_manager
from app.clock import Clock, FrozenClock
from app.config import AppEnv, Settings, get_settings, validate_startup
from app.demo.housing import HousingContext, housing_context
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import Assignment, Cleaner, CleanerScheduleVersion
from app.services.field_cipher import reveal_text
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.sms_lifecycle import drain_mock_sms, enqueue_schedule_messages, receive_inbound

DEMO_NOW = datetime(2026, 11, 10, 18, tzinfo=UTC)


def guard(settings: Settings) -> None:
    validate_startup(settings)
    database = make_url(settings.database_url)
    migration = make_url(settings.migration_database_url)
    cache = urlsplit(settings.redis_url)
    if (
        os.getenv("LOCAL_DEMO") != "1"
        or settings.app_env not in {AppEnv.development, AppEnv.test}
        or settings.production_dispatch_enabled
        or settings.production_integrations_enabled
        or database.host != "127.0.0.1"
        or database.database != "cleaning_demo"
        or (migration.host, migration.port, migration.database) != (database.host, database.port, database.database)
        or cache.hostname != "127.0.0.1"
        or cache.path != "/7"
    ):
        raise RuntimeError("Demo requires LOCAL_DEMO=1, loopback cleaning_demo database and Redis database 7.")


def token(settings: Settings, role: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": f"synthetic-{role}",
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "iat": now,
            "exp": now + timedelta(hours=8),
        },
        settings.auth_mock_secret,
        algorithm="HS256",
    )


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["manager", "owner", "viewer"]


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: uuid.UUID
    command: Literal["CONFIRM", "DONE", "UNAVAILABLE"]
    event_id: uuid.UUID


def build_demo_app(settings: Settings) -> FastAPI:
    guard(settings)
    app = create_app(settings, FrozenClock(DEMO_NOW))
    from app.demo.routing import install_driving_baseline

    install_driving_baseline(app)

    @app.get("/v1/demo/housing", response_model=HousingContext | None)
    def public_housing(_principal: Principal = Depends(require_manager)) -> HousingContext | None:
        return housing_context()

    @app.middleware("http")
    async def loopback_only(request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = None if request.client is None else request.client.host
        allowed = {"127.0.0.1", "::1"}
        if settings.app_env == AppEnv.test:
            allowed.add("testclient")
        if host not in allowed:
            return JSONResponse({"error": {"message": "Demo is loopback only."}}, status_code=403)
        return await call_next(request)

    @app.post("/v1/demo/session")
    def sign_in(payload: SignIn) -> dict[str, str]:
        return {"token": token(settings, payload.role)}

    @app.post("/v1/demo/record-sms")
    def record_sms(
        _principal: Principal = Depends(require_manager),
        session: Session = Depends(get_db),
        clock: Clock = Depends(get_clock),
    ) -> dict[str, object]:
        # Domain intent deduplication and row locks protect repeated clicks.
        enqueue_schedule_messages(session, clock=clock, settings=settings)
        result = drain_mock_sms(session, clock=clock, settings=settings)
        session.commit()
        return result

    @app.post("/v1/demo/reply")
    def reply(
        payload: Reply,
        principal: Principal = Depends(require_manager),
        session: Session = Depends(get_db),
        clock: Clock = Depends(get_clock),
    ) -> dict[str, object]:
        # Serialize demo commands before resolving their idempotency record.
        from sqlalchemy import text

        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('demo-reply-v1'))"))
        body = payload.model_dump(mode="json")
        digest = request_digest(body)
        key = str(payload.event_id)
        route = "/v1/demo/reply"
        resolution = resolve_idempotency(
            session,
            principal_subject=principal.subject,
            route=route,
            key=key,
            digest=digest,
            clock=clock,
        )
        if resolution.outcome == "replay" and resolution.body is not None:
            return resolution.body
        if resolution.outcome == "conflict":
            raise AppError("idempotency_conflict", "Demo event ID was already used.", 409)
        assignment = session.get(Assignment, payload.assignment_id)
        if assignment is None:
            raise AppError("not_found", "Assignment not found.", 404)
        cleaner = session.get(Cleaner, assignment.cleaner_id)
        version = session.get(CleanerScheduleVersion, assignment.cleaner_schedule_version_id)
        if cleaner is None or version is None or version.status != "current":
            raise AppError("stale_schedule", "Reload the current assignment before replying.", 409)
        command = {
            "CONFIRM": f"CONFIRM {assignment.service_date} V{version.version_number}",
            "DONE": f"DONE {assignment.task_id}",
            "UNAVAILABLE": f"UNAVAILABLE {assignment.service_date}",
        }[payload.command]
        result = receive_inbound(
            session,
            clock=clock,
            settings=settings,
            event=VerifiedSmsEvent(
                provider="mock",
                message_id=f"SM-DEMO-{key}",
                sender=reveal_text(cleaner.phone_ciphertext, settings.auth_mock_secret),
                body=command,
            ),
        )
        store_idempotency(
            session,
            settings=settings,
            clock=clock,
            principal_subject=principal.subject,
            route=route,
            key=key,
            digest=digest,
            status=200,
            body=result,
            resource_id=assignment.id,
        )
        session.commit()
        return result

    @app.get("/v1/demo/assignments")
    def assignments(
        _principal: Principal = Depends(require_manager),
        session: Session = Depends(get_db),
    ) -> dict[str, object]:
        rows = session.execute(
            select(Assignment, Cleaner.display_name)
            .join(Cleaner, Cleaner.id == Assignment.cleaner_id)
            .join(CleanerScheduleVersion, CleanerScheduleVersion.id == Assignment.cleaner_schedule_version_id)
            .where(CleanerScheduleVersion.status == "current")
            .order_by(Assignment.planned_start)
        ).all()
        return {
            "items": [
                {
                    "id": str(row.id),
                    "task_id": str(row.task_id),
                    "cleaner": name,
                    "status": row.status,
                    "service_date": str(row.service_date),
                }
                for row, name in rows
            ]
        }

    from app.demo.automation import install_automation

    install_automation(app, settings)
    return app


def factory() -> FastAPI:
    return build_demo_app(get_settings())
