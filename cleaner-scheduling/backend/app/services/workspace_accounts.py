from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from threading import BoundedSemaphore

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.errors import AppError
from app.models import OperatorProfile
from app.models.accounts import ManagerAccount, ManagerSession
from app.services.manager_mfa import consume_second_factor

_PASSWORD_SLOTS = BoundedSemaphore(2)
_DUMMY_HASH = "scrypt$" + "00" * 16 + "$" + "00" * 32


def password_hash(password: str, salt: bytes | None = None) -> str:
    if not _PASSWORD_SLOTS.acquire(blocking=False):
        raise AppError("authentication_busy", "Account access is busy. Try again shortly.", 503)
    try:
        salt = secrets.token_bytes(16) if salt is None else salt
        derived = hashlib.scrypt(password.encode(), salt=salt, n=131072, r=8, p=1, maxmem=268435456, dklen=32)
    finally:
        _PASSWORD_SLOTS.release()
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    encoded = stored or _DUMMY_HASH
    scheme, salt, _ = encoded.split("$")
    if scheme != "scrypt":
        raise ValueError("Unsupported password hash")
    matches = hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), encoded)
    return matches and stored is not None


def account_subject(account: ManagerAccount) -> str:
    return f"manager:{account.id}"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate_session(session: Session, token: str, now: datetime) -> ManagerAccount:
    if re.fullmatch(r"mgr_[A-Za-z0-9_-]{43,64}", token) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    account = session.scalar(
        select(ManagerAccount)
        .join(ManagerSession, ManagerSession.account_id == ManagerAccount.id)
        .where(
            ManagerAccount.deleted_at.is_(None),
            ManagerSession.token_hash == token_digest(token),
            ManagerSession.revoked_at.is_(None),
            ManagerSession.expires_at > now,
        )
    )
    if account is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    session.info["workspace_id"] = account.workspace_id
    session.execute(
        text("SELECT set_config('app.workspace_id', :workspace, true)"),
        {"workspace": str(account.workspace_id)},
    )
    operator = session.scalar(select(OperatorProfile).where(OperatorProfile.auth_subject == account_subject(account)))
    if operator is None or not operator.active or not {"owner", "manager"}.intersection(operator.roles):
        raise AppError("invalid_session", "Sign in to continue.", 401)
    return account


def create_session(session: Session, account: ManagerAccount, token: str, now: datetime) -> None:
    session.add(
        ManagerSession(
            token_hash=token_digest(token),
            account_id=account.id,
            created_at=now,
            expires_at=now + timedelta(hours=12),
            revoked_at=None,
        )
    )


def signup(
    session: Session,
    *,
    email: str,
    password: str,
    name: str,
    key: str,
    secret: str,
    now: datetime,
) -> tuple[ManagerAccount, str]:
    key_hash = token_digest(key)
    request_hash = hmac.new(
        secret.encode(),
        json.dumps([email, password, name], ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key_hash})
    account = session.scalar(select(ManagerAccount).where(ManagerAccount.signup_key_hash == key_hash).with_for_update())
    if account is not None:
        if account.deleted_at is not None:
            raise AppError("signup_unavailable", "This signup request is no longer available.", 409)
        if not hmac.compare_digest(account.signup_request_hash, request_hash):
            raise AppError("idempotency_conflict", "This signup key was used with different details.", 409)
        if account.mfa_secret is not None:
            raise AppError("mfa_required", "Sign in with your password and authenticator or recovery code.", 401)
        token = "mgr_" + hmac.new(secret.encode(), f"signup:{account.id}:{key}".encode(), hashlib.sha256).hexdigest()
        authenticate_session(session, token, now)
        return account, token
    encoded = password_hash(password)
    if session.scalar(select(ManagerAccount.id).where(ManagerAccount.email == email)) is not None:
        raise AppError("signup_unavailable", "Unable to create this account. Try signing in.", 409)
    workspace_id = uuid.uuid4()
    session.execute(
        text("INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, :name)"),
        {"id": workspace_id, "now": now, "name": name},
    )
    account = ManagerAccount(
        id=uuid.uuid4(),
        email=email,
        password_hash=encoded,
        workspace_id=workspace_id,
        created_at=now,
        signup_key_hash=key_hash,
        signup_request_hash=request_hash,
    )
    session.add(account)
    session.flush()
    session.info["workspace_id"] = workspace_id
    session.execute(text("SELECT set_config('app.workspace_id', :workspace, true)"), {"workspace": str(workspace_id)})
    session.add(
        OperatorProfile(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            auth_subject=account_subject(account),
            display_name=name,
            roles=["owner", "manager"],
            active=True,
        )
    )
    token = "mgr_" + hmac.new(secret.encode(), f"signup:{account.id}:{key}".encode(), hashlib.sha256).hexdigest()
    create_session(session, account, token, now)
    session.flush()
    return account, token


def login_session(
    session: Session,
    *,
    account: ManagerAccount,
    password: str,
    key: str,
    secret: str,
    now: datetime,
    second_factor: str = "",
) -> str:
    session.scalar(
        select(ManagerAccount)
        .where(ManagerAccount.id == account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    key_hash = token_digest(f"login:{account.id}:{key}")
    if account.mfa_secret is not None and not second_factor:
        raise AppError("mfa_required", "Enter an authenticator or recovery code.", 401)
    details = (
        json.dumps([password, second_factor, (account.mfa_secret or b"").hex()]).encode()
        if second_factor
        else password.encode()
    )
    request_hash = hmac.new(secret.encode(), details, hashlib.sha256).hexdigest()
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key_hash})
    existing = session.scalar(select(ManagerSession).where(ManagerSession.login_key_hash == key_hash))
    token = "mgr_" + hmac.new(secret.encode(), f"login:{account.id}:{key}".encode(), hashlib.sha256).hexdigest()
    if existing is not None:
        if existing.login_request_hash is None or not hmac.compare_digest(existing.login_request_hash, request_hash):
            raise AppError("idempotency_conflict", "This login key was used with different details.", 409)
    else:
        if account.mfa_secret is not None:
            consume_second_factor(account, second_factor, secret, now)
        session.add(
            ManagerSession(
                token_hash=token_digest(token),
                account_id=account.id,
                created_at=now,
                expires_at=now + timedelta(hours=12),
                revoked_at=None,
                login_key_hash=key_hash,
                login_request_hash=request_hash,
            )
        )
        session.flush()
    authenticate_session(session, token, now)
    return token
