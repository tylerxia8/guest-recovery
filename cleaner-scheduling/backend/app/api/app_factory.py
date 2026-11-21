from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.api.errors import install_error_handlers
from app.api.ingress import IngressGuard
from app.api.routes.cleaner_portal import router as cleaner_portal_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.email import router as email_router
from app.api.routes.health import router as health_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.local_scheduling import router as local_scheduling_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.policy import router as policy_router
from app.api.routes.schedules import router as schedules_router
from app.api.routes.setup import router as setup_router
from app.api.routes.sms import router as sms_router
from app.api.routes.tasks import router as tasks_router
from app.clock import Clock, SystemClock
from app.config import Settings, validate_startup
from app.db import get_engine, session_factory, validate_app_role
from app.logging_redaction import install_redaction
from app.workspaces import LEGACY_WORKSPACE_ID

logger = logging.getLogger("app.api")


def create_app(settings: Settings, clock: Clock | None = None) -> FastAPI:
    validate_startup(settings)
    install_redaction()
    app = FastAPI(
        title="Cleaning coordination API",
        version="0.1.0",
        docs_url=None if settings.protected_environment else "/docs",
        redoc_url=None,
        openapi_url=None if settings.protected_environment else "/openapi.json",
    )
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.state.engine = get_engine(settings.database_url)
    validate_app_role(app.state.engine)
    app.state.session_factory = session_factory(settings, LEGACY_WORKSPACE_ID)
    app.state.workspace_factories = {}
    app.state.redis = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    if settings.hospitable_read_only:
        from app.local_hospitable import lifespan

        app.router.lifespan_context = lifespan
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.api_allowed_host_list or ["127.0.0.1", "localhost", "hospitable-api"],
        )

        @app.middleware("http")
        async def read_only_workspace(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            path = request.url.path
            local_setup = request.method == "POST" and (
                path
                in {
                    "/v1/reservation-syncs",
                    "/v1/cleaners",
                    "/v1/cleaner-onboarding",
                    "/v1/cleaner-portal",
                    "/v1/cleaner-portal/language",
                    "/v1/email/test",
                    "/v1/email/sample-assignment",
                    "/v1/accounts/signup",
                    "/v1/accounts/login",
                    "/v1/accounts/logout",
                    "/v1/workspace/hospitable",
                    "/v1/workspace/cleaners",
                    "/v1/workspace/complete",
                }
                or re.fullmatch(r"/v1/email/(?:assignments|portal)/[0-9a-f-]{36}", path) is not None
                or re.fullmatch(
                    r"/v1/cleaners/[0-9a-f-]{36}/(?:weekly-hours|remove|eligibility|onboarding-link|email|languages)",
                    path,
                )
                is not None
            )
            local_schedule = (
                settings.local_scheduling_enabled
                and request.method == "POST"
                and (
                    path in {"/v1/schedules/previews", "/v1/local-scheduling/policy"}
                    or re.fullmatch(r"/v1/schedules/runs/[0-9a-f-]{36}/publish", path) is not None
                )
            )
            cleaning_time = (
                request.method == "PATCH" and re.fullmatch(r"/v1/properties/[0-9a-f-]{36}", path) is not None
            )
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and not local_setup
                and not cleaning_time
                and not local_schedule
                and not (request.method == "PATCH" and path == "/v1/accounts/me")
            ):
                from starlette.responses import JSONResponse

                return JSONResponse(
                    {"error": {"message": "Local setup, manual scheduling and email are available. SMS is disabled."}},
                    status_code=403,
                )
            return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID"],
    )
    app.add_middleware(IngressGuard, proxy_secret=settings.web_proxy_secret, clock=app.state.clock)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(policy_router)
    app.include_router(ingestion_router)
    app.include_router(tasks_router)
    app.include_router(schedules_router)
    app.include_router(sms_router)
    app.include_router(dashboard_router)
    app.include_router(setup_router)
    app.include_router(local_scheduling_router)
    app.include_router(email_router)
    app.include_router(onboarding_router)
    app.include_router(cleaner_portal_router)
    from app.api.routes.accounts import router as accounts_router
    from app.api.routes.workspace_setup import router as workspace_setup_router

    app.include_router(accounts_router)
    app.include_router(workspace_setup_router)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        header = request.headers.get("x-request-id")
        try:
            request_id = str(uuid.UUID(header)) if header else str(uuid.uuid4())
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        logged_path = request.url.path
        if "/cleaner-links/" in logged_path:
            logged_path = "/v1/cleaner-links/[redacted]"
        logger.info(
            "request completed method=%s path=%s status=%s request_id=%s",
            request.method,
            logged_path,
            response.status_code,
            request_id,
        )
        return response

    return app
