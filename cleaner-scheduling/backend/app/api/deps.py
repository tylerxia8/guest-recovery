from __future__ import annotations

import hmac
import re
import uuid
from collections.abc import Generator

import redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.auth.mock import MockAuthVerifier, TokenRejected
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import OperatorProfile
from app.services.audit import write_audit
from app.services.rate_limit import enforce_rate_limit

_bearer = HTTPBearer(auto_error=False)
_IDEMPOTENCY = re.compile(r"[\x21-\x7E]{1,128}")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_db(request: Request) -> Generator[Session, None, None]:
    settings: Settings = request.app.state.settings
    factory = request.app.state.session_factory
    if settings.workspace_signup_enabled:
        from app.db import catalog_session_factory, session_factory
        from app.services.workspace_accounts import account_subject, authenticate_session
        from app.workspaces import LEGACY_WORKSPACE_ID

        remote = getattr(request.state, "client_address", "unknown" if request.client is None else request.client.host)
        enforce_rate_limit(
            request.app.state.redis,
            clock=request.app.state.clock,
            subject=f"workspace-access:{remote}",
            bucket="public",
            limit=120,
        )
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if request.url.path in {"/v1/cleaner-portal", "/v1/cleaner-portal/language", "/v1/cleaner-onboarding"}:
            if re.fullmatch(r"(?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}", token) is None:
                raise AppError("link_unavailable", "Open your original private cleaner link.", 401)
            workspace_id = uuid.UUID(token.split(".")[0]) if token.count(".") == 2 else LEGACY_WORKSPACE_ID
            grant_id = uuid.UUID(token.split(".")[-2])
            if request.url.path == "/v1/cleaner-onboarding":
                from app.api.routes.onboarding import onboarding_token

                expected = onboarding_token(grant_id, settings, workspace_id)
            else:
                from app.services.cleaner_coordination import portal_token

                expected = portal_token(grant_id, settings, workspace_id)
            if not hmac.compare_digest(expected, token):
                raise AppError("link_unavailable", "Open your original private cleaner link.", 401)
        else:
            with catalog_session_factory(settings)() as catalog:
                account = authenticate_session(catalog, token, request.app.state.clock.now())
                workspace_id = account.workspace_id
                request.state.account_subject = account_subject(account)
        factories = request.app.state.workspace_factories
        if workspace_id not in factories:
            factories[workspace_id] = session_factory(settings, workspace_id=workspace_id)
        factory = factories[workspace_id]
    session: Session = factory()
    request.state.session = session
    try:
        if (
            settings.workspace_signup_enabled
            and request.url.path not in {"/v1/cleaner-portal", "/v1/cleaner-portal/language", "/v1/cleaner-onboarding"}
            and not request.url.path.startswith("/v1/workspace/")
        ):
            from app.models.workspace_connection import WorkspaceConnection

            connection = session.get(WorkspaceConnection, session.info["workspace_id"])
            if connection is None or connection.completed_at is None:
                raise AppError("setup_required", "Finish connecting Hospitable and adding cleaners to continue.", 403)
        yield session
    finally:
        session.close()


def get_redis(request: Request) -> redis.Redis:
    client: redis.Redis = request.app.state.redis
    return client


def _authorize(
    request: Request,
    session: Session,
    settings: Settings,
    clock: Clock,
    cache: redis.Redis,
    credentials: HTTPAuthorizationCredentials | None,
    allowed_roles: set[str],
    denial_message: str,
) -> Principal:
    correlation_id = uuid.UUID(request.state.request_id)
    if credentials is None or not credentials.credentials:
        _deny(
            session,
            clock=clock,
            request=request,
            correlation_id=correlation_id,
            subject=None,
            code="missing_token",
            status_code=401,
        )
        raise AppError("missing_token", "Authentication is required.", 401)
    try:
        if settings.workspace_signup_enabled:
            profile = _lookup(session, request.state.account_subject)
            if profile is None or not profile[2]:
                raise AppError("account_unavailable", "This account is unavailable.", 401)
            principal = Principal(subject=profile[0], roles=profile[1])
        else:
            verifier = MockAuthVerifier(settings.auth_mock_secret, settings.auth_issuer, settings.auth_audience)
            principal = verifier.verify(credentials.credentials, lambda subject: _lookup(session, subject))
    except TokenRejected as exc:
        _deny(
            session,
            clock=clock,
            request=request,
            correlation_id=correlation_id,
            subject=exc.subject,
            code=exc.code,
            status_code=exc.status_code,
        )
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    if allowed_roles.isdisjoint(principal.roles):
        _deny(
            session,
            clock=clock,
            request=request,
            correlation_id=correlation_id,
            subject=principal.subject,
            code="insufficient_role",
            status_code=403,
        )
        raise AppError("insufficient_role", denial_message, 403)
    bucket = "write" if request.method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
    limit = settings.write_rate_limit_per_minute if bucket == "write" else settings.read_rate_limit_per_minute
    enforce_rate_limit(cache, clock=clock, subject=principal.subject, bucket=bucket, limit=limit)
    request.state.principal = principal
    return principal


def require_owner(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    return _authorize(request, session, settings, clock, cache, credentials, {"owner"}, "Owner role is required.")


def require_manager(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    return _authorize(
        request,
        session,
        settings,
        clock,
        cache,
        credentials,
        {"manager", "owner"},
        "Manager role is required.",
    )


def require_reader(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    return _authorize(
        request,
        session,
        settings,
        clock,
        cache,
        credentials,
        {"viewer", "manager", "owner"},
        "A reader role is required.",
    )


def require_sync_operator(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    return _authorize(
        request,
        session,
        settings,
        clock,
        cache,
        credentials,
        {"manager", "owner", "scheduler"},
        "Manager or scheduler role is required.",
    )


def _idempotency_header(request: Request, principal: Principal, session: Session, clock: Clock) -> str:
    raw = request.headers.get("idempotency-key")
    if raw is None or _IDEMPOTENCY.fullmatch(raw) is None:
        code = "idempotency_key_required" if raw is None else "idempotency_key_invalid"
        _malformed(session, clock=clock, request=request, subject=principal.subject, code=code)
        raise AppError(
            code,
            "Idempotency-Key must be 1-128 printable ASCII characters without spaces.",
            400,
        )
    return raw


def require_idempotency_key(
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> str:
    return _idempotency_header(request, principal, session, clock)


def require_manager_idempotency_key(
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> str:
    return _idempotency_header(request, principal, session, clock)


def require_sync_idempotency_key(
    request: Request,
    principal: Principal = Depends(require_sync_operator),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> str:
    return _idempotency_header(request, principal, session, clock)


def require_if_match(
    request: Request,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> int:
    raw = request.headers.get("if-match")
    if raw is None or raw.strip() == "":
        _malformed(session, clock=clock, request=request, subject=principal.subject, code="if_match_required")
        raise AppError("if_match_required", "If-Match is required.", 428)
    value = raw.strip()
    if value.startswith("W/"):
        _malformed(session, clock=clock, request=request, subject=principal.subject, code="if_match_invalid")
        raise AppError("if_match_invalid", "Weak validators are not accepted.", 428)
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if not value.isdigit() or int(value) < 1:
        _malformed(session, clock=clock, request=request, subject=principal.subject, code="if_match_invalid")
        raise AppError("if_match_invalid", "If-Match must be a positive integer version.", 428)
    return int(value)


def _malformed(
    session: Session,
    *,
    clock: Clock,
    request: Request,
    subject: str,
    code: str,
) -> None:
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=subject,
        action="request.malformed",
        entity_type="http_route",
        entity_id=None,
        correlation_id=uuid.UUID(request.state.request_id),
        reason=code,
        redacted_changes={"http_method": request.method, "route": request.url.path},
        request_id=request.state.request_id,
    )
    session.commit()


def _lookup(session: Session, subject: str) -> tuple[str, tuple[str, ...], bool] | None:
    row = session.scalar(select(OperatorProfile).where(OperatorProfile.auth_subject == subject))
    if row is None:
        return None
    return row.auth_subject, tuple(row.roles), row.active


def _deny(
    session: Session,
    *,
    clock: Clock,
    request: Request,
    correlation_id: uuid.UUID,
    subject: str | None,
    code: str,
    status_code: int,
) -> None:
    write_audit(
        session,
        clock=clock,
        actor_type="operator" if subject else "anonymous",
        actor_subject=subject or "anonymous",
        action="security.access_denied",
        entity_type="http_route",
        entity_id=None,
        correlation_id=correlation_id,
        reason=code,
        redacted_changes={
            "http_method": request.method,
            "route": request.url.path,
            "status_code": status_code,
        },
        request_id=request.state.request_id,
    )
    session.commit()
