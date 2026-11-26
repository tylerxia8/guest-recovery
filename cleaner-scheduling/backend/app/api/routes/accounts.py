from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from collections.abc import Generator
from typing import Annotated, Literal, cast

import redis
from app.api.deps import get_clock, get_redis, get_settings
from app.clock import Clock
from app.config import Settings
from app.db import catalog_session_factory
from app.domain.errors import AppError
from app.models import OperatorProfile
from app.models.accounts import ManagerAccount, ManagerInvitation, ManagerSession
from app.services.account_deletion import delete_account
from app.services.email_addresses import normalize_email
from app.services.manager_mfa import change_mfa
from app.services.operations_access import (
    ACCESS_SECONDS,
    issue_access,
    issue_read_access,
    issue_repair_access,
    issue_touchup_access,
    read_session_digest,
)
from app.services.security_alerts import queue_security_alert, record_failed_login
from app.services.workspace_accounts import (
    account_subject,
    authenticate_session,
    authenticate_session_digest,
    lock_active_workspace,
    login_session,
    signup,
    token_digest,
    verify_password,
    workspace_owner_id,
)
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import or_, select, text, update
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


class OperationsGrantWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: Literal["guest-recovery", "maintenance"]
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


class OperationsRedeemWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: Literal["guest-recovery", "maintenance"]
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


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
    login = session.get(ManagerAccount, account.login_account_id) if account.login_account_id else account
    return {
        "email": login.email if login is not None else "",
        "primary_login": account.login_account_id is None,
        "workspace_id": str(account.workspace_id),
        "name": name,
        "workspace_owner": account.workspace_owner,
        "mfa_enabled": login is not None and login.mfa_secret is not None,
        "recovery_codes_remaining": len(login.mfa_recovery_hashes) if login is not None else 0,
    }


@router.post("/signup")
def create_account(
    payload: SignupWrite,
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    team_invite = payload.invitation_token if (payload.invitation_token or "").startswith("team_") else None
    if not settings.workspace_registration_enabled and team_invite is None:
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
            team_invite=team_invite,
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
    account = session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.email == payload.email, ManagerAccount.login_account_id.is_(None))
        .with_for_update()
    )
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
    target = account
    if account.home_access_revoked_at is not None:
        fallback = session.scalar(
            select(ManagerAccount)
            .where(
                ManagerAccount.login_account_id == account.id,
                ManagerAccount.deleted_at.is_(None),
                ManagerAccount.home_access_revoked_at.is_(None),
            )
            .order_by(ManagerAccount.created_at, ManagerAccount.id)
            .limit(1)
        )
        if fallback is None:
            raise AppError("invalid_credentials", "Email or password is incorrect.", 401)
        target = fallback
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
            target=target,
        )
    except AppError as error:
        if error.code == "invalid_second_factor":
            record_failed_login(session, cache, account.id, account.workspace_id, settings, clock)
            session.commit()
        raise
    if len(account.mfa_recovery_hashes) < recovery_count:
        queue_security_alert(session, account.id, session.info["workspace_id"], "recovery_code_used", settings, clock)
    selected = authenticate_session(session, token, clock.now())
    result = {**account_response(session, selected), "token": token}
    session.commit()
    logger.info("account.login", extra={"workspace_id": str(account.workspace_id)})
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


def operations_grant_key(client: str, code: str, challenge: str) -> str:
    return f"operations-login:{client}:{token_digest(code)}:{challenge}"


@router.post("/operations/grants", include_in_schema=False)
def issue_operations_grant(
    payload: OperationsGrantWrite,
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    if not settings.operations_login_enabled:
        raise AppError("not_found", "Not found.", 404)
    token = bearer_token(request)
    account = authenticate_session(session, token, clock.now())
    account_rate_limit(cache, f"operations-grants:{account.id}", 15)
    code = secrets.token_urlsafe(32)
    try:
        saved = cache.set(
            operations_grant_key(payload.client, code, payload.challenge),
            token_digest(token),
            ex=60,
            nx=True,
        )
    except RedisError as exc:
        raise AppError("dependency_unavailable", "Shared sign-in is temporarily unavailable.", 503) from exc
    if not saved:
        raise AppError("dependency_unavailable", "Shared sign-in is temporarily unavailable.", 503)
    return JSONResponse(content={"code": code}, headers={"Cache-Control": "no-store"})


@router.post("/operations/redeem", include_in_schema=False)
def redeem_operations_grant(
    payload: OperationsRedeemWrite,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    if not settings.operations_login_enabled:
        raise AppError("not_found", "Not found.", 404)
    verifier_digest = hashlib.sha256(payload.verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(verifier_digest).decode("ascii").rstrip("=")
    try:
        digest = cache.getdel(operations_grant_key(payload.client, payload.code, challenge))
    except RedisError as exc:
        raise AppError("dependency_unavailable", "Shared sign-in is temporarily unavailable.", 503) from exc
    if isinstance(digest, bytes):
        digest = digest.decode("ascii")
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise AppError("invalid_grant", "Shared sign-in expired. Start again.", 401)
    account = authenticate_session_digest(session, digest, clock.now())
    owner = workspace_owner_id(session, account.workspace_id)
    return JSONResponse(
        content={
            "account_id": str(account.id),
            "workspace_id": str(account.workspace_id),
            "workspace_owner_id": str(owner),
            "membership_token": issue_access(cache, digest, "opm_", ttl=43200),
            **(
                {"reply_token": issue_access(cache, digest, "opg_")}
                if payload.client == "guest-recovery"
                and settings.operations_guest_reply_workspace == account.workspace_id
                else {}
            ),
            **(
                {"read_token": issue_read_access(cache, digest), "read_token_expires_in": ACCESS_SECONDS}
                if payload.client == "guest-recovery"
                else {}
            ),
            **(
                {"touchup_token": issue_touchup_access(cache, digest)}
                if payload.client == "guest-recovery" and settings.operations_touchup_enabled
                else {}
            ),
            **(
                {"repair_token": issue_repair_access(cache, digest)}
                if payload.client == "guest-recovery" and settings.operations_repair_enabled
                else {}
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/operations/membership", include_in_schema=False)
def operations_membership(
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    if not settings.operations_login_enabled:
        raise AppError("not_found", "Not found.", 404)
    token = bearer_token(request)
    if re.fullmatch(r"opm_[A-Za-z0-9_-]{43}", token) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    account = authenticate_session_digest(session, read_session_digest(cache, token), clock.now())
    return JSONResponse(
        content={"workspace_id": str(account.workspace_id), "account_id": str(account.id)},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/operations/inbox-grant", include_in_schema=False)
def operations_inbox_grant(
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    if not settings.operations_login_enabled:
        raise AppError("not_found", "Not found.", 404)
    token = bearer_token(request)
    authenticate_session(session, token, clock.now())
    return JSONResponse(
        content={"token": issue_access(cache, token_digest(token), "opq_", ttl=60)},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/operations/inbox-identity", include_in_schema=False)
def operations_inbox_identity(
    request: Request,
    session: Session = Depends(account_database),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    cache: redis.Redis = Depends(get_redis),
) -> JSONResponse:
    if not settings.operations_login_enabled:
        raise AppError("not_found", "Not found.", 404)
    token = bearer_token(request)
    if re.fullmatch(r"opq_[A-Za-z0-9_-]{43}", token) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    account = authenticate_session_digest(session, read_session_digest(cache, token), clock.now())
    return JSONResponse(
        content={
            "workspace_id": str(account.workspace_id),
            "account_id": str(account.id),
            "workspace_owner_id": str(workspace_owner_id(session, account.workspace_id)),
        },
        headers={"Cache-Control": "no-store"},
    )


class JoinWorkspaceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_token: str = Field(pattern=r"^team_[A-Za-z0-9_-]{43}$", repr=False)


class SwitchWorkspaceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: uuid.UUID


@router.post("/join", include_in_schema=False)
def join_workspace(
    payload: JoinWorkspaceWrite,
    request: Request,
    session: Session = Depends(account_database),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    now = clock.now()
    account = authenticate_session(session, bearer_token(request), now)
    profile = session.scalar(select(OperatorProfile).where(OperatorProfile.auth_subject == account_subject(account)))
    if profile is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    login_id = account.login_account_id or account.id
    login = session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.id == login_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    invitation_hash = token_digest(payload.invitation_token)
    # Catalog lookup must not inherit the currently selected workspace's RLS context.
    session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
    invite = session.get(ManagerInvitation, invitation_hash)
    if (
        login is None
        or login.deleted_at is not None
        or invite is None
        or invite.email != login.email
        or invite.expires_at <= now
    ):
        raise AppError("invitation_required", "This invitation is unavailable for your signed-in email.", 403)
    workspace_id = invite.workspace_id
    if not lock_active_workspace(session, workspace_id):
        raise AppError("invitation_required", "This invitation is unavailable.", 403)
    invite = session.get(ManagerInvitation, invitation_hash, populate_existing=True)
    if invite is None or invite.expires_at <= now or invite.email != login.email:
        raise AppError("invitation_required", "This invitation is unavailable.", 403)
    if (
        session.scalar(
            select(ManagerAccount.id).where(
                ManagerAccount.workspace_id == workspace_id,
                ManagerAccount.deleted_at.is_(None),
                ManagerAccount.home_access_revoked_at.is_(None),
                or_(ManagerAccount.id == login_id, ManagerAccount.login_account_id == login_id),
            )
        )
        is not None
    ):
        raise AppError("already_in_workspace", "You already belong to this workspace.", 409)
    member_id = uuid.uuid4()
    member = ManagerAccount(
        id=member_id,
        email=f"{member_id}@workspace.invalid",
        password_hash="",
        workspace_id=workspace_id,
        workspace_owner=False,
        login_account_id=login_id,
        created_at=now,
        signup_key_hash=token_digest(secrets.token_urlsafe(32)),
        signup_request_hash="",
    )
    session.execute(text("SELECT set_config('app.workspace_id', :id, true)"), {"id": str(workspace_id)})
    session.add(member)
    session.add(
        OperatorProfile(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            auth_subject=f"manager:{member_id}",
            display_name=profile.display_name,
            roles=["manager"],
            active=True,
        )
    )
    session.delete(invite)
    session.commit()
    return JSONResponse(content={"workspace_id": str(workspace_id)}, headers={"Cache-Control": "no-store"})


@router.get("/workspaces", include_in_schema=False)
def account_workspaces(
    request: Request, session: Session = Depends(account_database), clock: Clock = Depends(get_clock)
) -> JSONResponse:
    account = authenticate_session(session, bearer_token(request), clock.now())
    primary = account.login_account_id or account.id
    rows = session.execute(
        text("""
        SELECT w.id, w.name FROM workspaces w JOIN manager_accounts a ON a.workspace_id=w.id
        WHERE (a.id=:login OR a.login_account_id=:login) AND a.deleted_at IS NULL
          AND a.home_access_revoked_at IS NULL AND w.deleted_at IS NULL
        ORDER BY w.name, w.id
    """),
        {"login": primary},
    ).mappings()
    return JSONResponse(
        content={
            "current": str(account.workspace_id),
            "items": [{"id": str(row["id"]), "name": row["name"]} for row in rows],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/switch-workspace", include_in_schema=False)
def switch_workspace(
    payload: SwitchWorkspaceWrite,
    request: Request,
    session: Session = Depends(account_database),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    now = clock.now()
    digest = token_digest(bearer_token(request))
    account = authenticate_session_digest(session, digest, now)
    login_id = account.login_account_id or account.id
    login = session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.id == login_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if login is None or login.deleted_at is not None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    source = session.scalar(select(ManagerSession).where(ManagerSession.token_hash == digest).with_for_update())
    if source is None or source.revoked_at is not None or source.expires_at <= now:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    authenticate_session_digest(session, digest, now)
    target = session.scalar(
        select(ManagerAccount).where(
            ManagerAccount.workspace_id == payload.workspace_id,
            ManagerAccount.deleted_at.is_(None),
            ManagerAccount.home_access_revoked_at.is_(None),
            or_(ManagerAccount.id == login_id, ManagerAccount.login_account_id == login_id),
        )
    )
    if target is None:
        raise AppError("workspace_unavailable", "This workspace is unavailable.", 403)
    token = "mgr_" + secrets.token_urlsafe(32)
    session.add(
        ManagerSession(
            token_hash=token_digest(token),
            account_id=target.id,
            created_at=now,
            expires_at=source.expires_at,
        )
    )
    session.flush()
    authenticate_session(session, token, now)
    source.revoked_at = now
    result = {**account_response(session, target), "token": token}
    session.commit()
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
    login_id = account.login_account_id or account.id
    account_rate_limit(cache, f"mfa:{login_id}", 10)
    login = session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.id == login_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    authenticate_session(session, token, clock.now())
    if login is None or login.deleted_at is not None or not verify_password(payload.password, login.password_hash):
        logger.info("account.mfa_denied")
        raise AppError("invalid_credentials", "Password is incorrect.", 401)
    result = change_mfa(
        session,
        account=login,
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
