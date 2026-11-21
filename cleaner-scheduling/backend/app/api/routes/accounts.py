from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Generator
from typing import Annotated, Literal, cast

import redis
from app.api.deps import get_clock, get_redis, get_settings
from app.clock import Clock
from app.config import Settings
from app.db import catalog_session_factory
from app.domain.errors import AppError
from app.models.accounts import ManagerAccount, ManagerSession
from app.services.account_deletion import delete_account
from app.services.email_addresses import normalize_email
from app.services.manager_mfa import change_mfa
from app.services.security_alerts import queue_security_alert, record_failed_login
from app.services.workspace_accounts import authenticate_session, login_session, signup, token_digest, verify_password
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])
logger = logging.getLogger(__name__)


class LoginWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128, repr=False)
    second_factor: str = Field(default="", max_length=64, repr=False)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if normalized is None:
            raise ValueError("Enter a valid email address.")
        return normalized.lower()


class SignupWrite(LoginWrite):
    invitation_token: str | None = Field(default=None, min_length=32, max_length=256, repr=False)
    password: str = Field(min_length=12, max_length=128, repr=False)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Enter your name.")
        return value


class BeginMfaWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["begin_mfa"]
    password: str = Field(min_length=1, max_length=128, repr=False)


class VerifyMfaWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm_mfa", "disable_mfa"]
    password: str = Field(min_length=1, max_length=128, repr=False)
    second_factor: str = Field(min_length=1, max_length=64, repr=False)


class DeleteAccountWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_account"]
    confirmation: Literal["DELETE"]
    password: str = Field(min_length=1, max_length=128, repr=False)
    second_factor: str = Field(default="", max_length=64, repr=False)


def account_rate_limit(cache: redis.Redis, key: str, limit: int) -> None:
    script = (
        "local count = redis.call('INCR', KEYS[1]); "
        "if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end; return count"
    )
    try:
        count = int(cast(int, cache.eval(script, 1, f"account-rate:{key}")))
    except RedisError as exc:
        raise AppError("dependency_unavailable", "Account access is temporarily unavailable.", 503) from exc
    if count > limit:
        raise AppError("rate_limited", "Too many requests. Try again in a minute.", 429)


def account_database(
    request: Request,
    settings: Settings = Depends(get_settings),
    cache: redis.Redis = Depends(get_redis),
) -> Generator[Session, None, None]:
    if not settings.workspace_signup_enabled:
        raise AppError("not_found", "Not found.", 404)
    remote = getattr(request.state, "client_address", "unknown" if request.client is None else request.client.host)
    if remote == "unknown":
        raise AppError("client_unavailable", "Unable to identify this request.", 503)
    address = hashlib.sha256(remote.encode()).hexdigest()
    bucket = "auth" if request.url.path.endswith(("/login", "/signup")) or request.method == "PATCH" else "session"
    account_rate_limit(cache, f"{bucket}:{address}", 10 if bucket == "auth" else 120)
    with catalog_session_factory(settings)() as session:
        yield session


def bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise AppError("invalid_session", "Sign in to continue.", 401)
    return authorization.removeprefix("Bearer ")


def account_response(session: Session, account: ManagerAccount) -> dict[str, object]:
    name = session.scalar(text("SELECT name FROM workspaces WHERE id = :id"), {"id": account.workspace_id})
    if not isinstance(name, str):
        raise RuntimeError("Account workspace is missing")
    return {
        "email": account.email,
        "workspace_id": str(account.workspace_id),
        "name": name,
        "mfa_enabled": account.mfa_secret is not None,
        "recovery_codes_remaining": len(account.mfa_recovery_hashes),
    }


@router.post("/signup")
def create_account(
    payload: SignupWrite,
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    if not settings.workspace_registration_enabled:
        invitation_matches = hmac.compare_digest(
            hashlib.sha256((payload.invitation_token or "").encode()).hexdigest(), settings.workspace_invite_token_hash
        )
        if not (
            settings.workspace_invite_signup_enabled
            and payload.email == settings.workspace_invite_email
            and invitation_matches
            and settings.workspace_invite_expires_at is not None
            and settings.workspace_invite_expires_at > clock.now()
        ):
            logger.info("account.invitation_denied")
            raise AppError(
                "invitation_required", "A valid invitation for this email is required. Contact your inviter.", 403
            )
    key = request.headers.get("idempotency-key", "")
    if re.fullmatch(r"[\x21-\x7E]{1,128}", key) is None:
        raise AppError("idempotency_key_invalid", "A valid Idempotency-Key is required.", 400)
    try:
        account, token = signup(
            session,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            key=key,
            secret=settings.field_encryption_secret,
            now=clock.now(),
        )
        result = {**account_response(session, account), "token": token}
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError("signup_unavailable", "Unable to create this account. Try signing in.", 409) from exc
    logger.info("account.signup", extra={"workspace_id": str(account.workspace_id)})
    return JSONResponse(status_code=201, content=result, headers={"Cache-Control": "no-store"})


@router.post("/login")
def login(
    payload: LoginWrite,
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    key = request.headers.get("idempotency-key", "")
    if re.fullmatch(r"[\x21-\x7E]{1,128}", key) is None:
        raise AppError("idempotency_key_invalid", "A valid Idempotency-Key is required.", 400)
    account_rate_limit(cache, f"login:{hashlib.sha256(payload.email.encode()).hexdigest()}", 10)
    account = session.scalar(select(ManagerAccount).where(ManagerAccount.email == payload.email).with_for_update())
    valid = verify_password(payload.password, None if account is None else account.password_hash)
    if not valid or account is None or account.deleted_at is not None:
        logger.info("account.login_denied")
        if account is not None and account.deleted_at is None:
            try:
                record_failed_login(session, cache, account.id, account.workspace_id, settings, clock)
                session.commit()
            except Exception:
                session.rollback()
                logger.error("security_alert.login_record_failed")
        raise AppError("invalid_credentials", "Email or password is incorrect.", 401)
    recovery_count = len(account.mfa_recovery_hashes)
    try:
        token = login_session(
            session,
            account=account,
            password=payload.password,
            key=key,
            secret=settings.field_encryption_secret,
            now=clock.now(),
            second_factor=payload.second_factor,
        )
    except AppError as error:
        if error.code == "invalid_second_factor":
            record_failed_login(session, cache, account.id, account.workspace_id, settings, clock)
            session.commit()
        raise
    if len(account.mfa_recovery_hashes) < recovery_count:
        queue_security_alert(session, account.id, account.workspace_id, "recovery_code_used", settings, clock)
    result = {**account_response(session, account), "token": token}
    session.commit()
    logger.info("account.login", extra={"workspace_id": str(account.workspace_id)})
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get("/me")
def current_account(
    request: Request,
    session: Session = Depends(account_database),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    account = authenticate_session(session, bearer_token(request), clock.now())
    return JSONResponse(content=account_response(session, account), headers={"Cache-Control": "no-store"})


@router.patch("/me")
def update_account(
    payload: Annotated[BeginMfaWrite | VerifyMfaWrite | DeleteAccountWrite, Field(discriminator="action")],
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    key = request.headers.get("idempotency-key", "")
    if re.fullmatch(r"[\x21-\x7E]{1,128}", key) is None:
        raise AppError("idempotency_key_invalid", "A valid Idempotency-Key is required.", 400)
    token = bearer_token(request)
    if isinstance(payload, DeleteAccountWrite):
        account_rate_limit(cache, f"delete:{token_digest(token)}", 10)
        result = delete_account(
            session,
            token=token,
            password=payload.password,
            second_factor=payload.second_factor,
            key=key,
            settings=settings,
            clock=clock,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
    account = authenticate_session(session, token, clock.now())
    account_rate_limit(cache, f"mfa:{account.id}", 10)
    session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.id == account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    authenticate_session(session, token, clock.now())
    if not verify_password(payload.password, account.password_hash):
        logger.info("account.mfa_denied")
        raise AppError("invalid_credentials", "Password is incorrect.", 401)
    result = change_mfa(
        session,
        account=account,
        action=payload.action,
        password=payload.password,
        second_factor=payload.second_factor if isinstance(payload, VerifyMfaWrite) else "",
        session_digest=token_digest(token),
        key=key,
        settings=settings,
        clock=clock,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    logger.info("account.mfa_changed", extra={"action": payload.action, "workspace_id": str(account.workspace_id)})
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.post("/logout")
def logout(
    request: Request,
    session: Session = Depends(account_database),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    token = bearer_token(request)
    if re.fullmatch(r"mgr_[A-Za-z0-9_-]{43,64}", token) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    session.execute(
        update(ManagerSession)
        .where(
            ManagerSession.token_hash == token_digest(token),
            ManagerSession.revoked_at.is_(None),
        )
        .values(revoked_at=clock.now())
    )
    session.commit()
    logger.info("account.logout")
    return JSONResponse(content={"signed_out": True}, headers={"Cache-Control": "no-store"})
