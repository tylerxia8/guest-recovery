from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models.accounts import ManagerAccount, ManagerSession
from app.services.audit import write_audit
from app.services.field_cipher import protect_text, reveal_text
from app.services.idempotency import resolve_idempotency, store_idempotency
from app.services.security_alerts import queue_security_alert

MfaAction = Literal["begin_mfa", "confirm_mfa", "disable_mfa"]


def totp_counter(seed: str, factor: str, now: datetime, last_counter: int | None) -> int | None:
    if re.fullmatch(r"[0-9]{6}", factor) is None:
        return None
    key = base64.b32decode(seed)
    current = int(now.timestamp()) // 30
    for counter in (current, current - 1, current + 1):
        if counter < 0 or (last_counter is not None and counter <= last_counter):
            continue
        digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
        offset = digest[-1] & 15
        code = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000
        if hmac.compare_digest(f"{code:06d}", factor):
            return counter
    return None


def recovery_digest(account: ManagerAccount, code: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"mfa-recovery:{account.id}:{code}".encode(), hashlib.sha256).hexdigest()


def consume_second_factor(account: ManagerAccount, factor: str, secret: str, now: datetime) -> None:
    if account.mfa_secret is None:
        raise RuntimeError("Second factor verification requires active MFA")
    if not factor:
        raise AppError("mfa_required", "Enter an authenticator or recovery code.", 401)
    counter = totp_counter(reveal_text(account.mfa_secret, secret), factor, now, account.mfa_last_counter)
    if counter is not None:
        account.mfa_last_counter = counter
        return
    normalized = factor.replace("-", "").lower()
    if re.fullmatch(r"[0-9a-f]{32}", normalized) is not None:
        digest = recovery_digest(account, normalized, secret)
        for stored in account.mfa_recovery_hashes:
            if hmac.compare_digest(stored, digest):
                account.mfa_recovery_hashes = [value for value in account.mfa_recovery_hashes if value != stored]
                return
    raise AppError(
        "invalid_second_factor", "The authenticator or recovery code is invalid or has already been used.", 401
    )


def change_mfa(
    session: Session,
    *,
    account: ManagerAccount,
    action: MfaAction,
    password: str,
    second_factor: str,
    session_digest: str,
    key: str,
    settings: Settings,
    clock: Clock,
    request_id: str | None,
) -> dict[str, object]:
    now = clock.now()
    secret = settings.field_encryption_secret
    principal = f"manager:{account.id}"
    route = "PATCH /v1/accounts/me"
    digest = hmac.new(
        secret.encode(), json.dumps([action, password, second_factor, session_digest]).encode(), hashlib.sha256
    ).hexdigest()
    if action == "begin_mfa" and account.mfa_secret is not None:
        raise AppError("mfa_already_enabled", "Multi-factor authentication is already enabled.", 409)
    resolution = resolve_idempotency(
        session, principal_subject=principal, route=route, key=key, digest=digest, clock=clock
    )
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "This key was used with different account details.", 409)
    state = hashlib.sha256((account.mfa_secret or b"") + (account.mfa_pending_secret or b"")).hexdigest()
    if resolution.outcome == "replay":
        if resolution.body is None:
            raise RuntimeError("MFA idempotency response is missing")
        encrypted = resolution.body["encrypted_response"]
        if not isinstance(encrypted, str):
            raise RuntimeError("MFA idempotency response is invalid")
        saved = json.loads(reveal_text(bytes.fromhex(encrypted), secret))
        if datetime.fromisoformat(saved["expires_at"]) <= now or saved["state"] != state:
            raise AppError("idempotency_expired", "This account action expired. Start again with a new request.", 409)
        return cast(dict[str, object], saved["body"])
    result: dict[str, object]
    if action == "begin_mfa":
        seed = base64.b32encode(secrets.token_bytes(20)).decode()
        account.mfa_pending_secret = protect_text(seed, secret)
        account.mfa_pending_session_hash = session_digest
        account.mfa_pending_expires_at = now + timedelta(minutes=10)
        result = {"mfa_enabled": False, "secret": seed, "expires_at": account.mfa_pending_expires_at.isoformat()}
    elif action == "confirm_mfa":
        if account.mfa_secret is not None:
            raise AppError("mfa_already_enabled", "Multi-factor authentication is already enabled.", 409)
        if (
            account.mfa_pending_secret is None
            or account.mfa_pending_expires_at is None
            or account.mfa_pending_expires_at <= now
            or account.mfa_pending_session_hash is None
            or not hmac.compare_digest(account.mfa_pending_session_hash, session_digest)
        ):
            raise AppError("mfa_setup_expired", "Start authenticator setup again.", 409)
        counter = totp_counter(reveal_text(account.mfa_pending_secret, secret), second_factor, now, None)
        if counter is None:
            raise AppError("invalid_second_factor", "Enter a valid code from your authenticator.", 401)
        codes = [secrets.token_hex(16) for _ in range(8)]
        account.mfa_secret = account.mfa_pending_secret
        account.mfa_pending_secret = None
        account.mfa_pending_session_hash = None
        account.mfa_pending_expires_at = None
        account.mfa_last_counter = counter
        account.mfa_recovery_hashes = [recovery_digest(account, code, secret) for code in codes]
        result = {
            "mfa_enabled": True,
            "recovery_codes": ["-".join(code[i : i + 4] for i in range(0, 32, 4)) for code in codes],
        }
    else:
        if account.mfa_secret is None:
            raise AppError("mfa_not_enabled", "Multi-factor authentication is not enabled.", 409)
        consume_second_factor(account, second_factor, secret, now)
        account.mfa_secret = None
        account.mfa_pending_secret = None
        account.mfa_pending_session_hash = None
        account.mfa_pending_expires_at = None
        account.mfa_last_counter = None
        account.mfa_recovery_hashes = []
        result = {"mfa_enabled": False, "recovery_codes_remaining": 0}
    if action != "begin_mfa":
        session.execute(
            update(ManagerSession)
            .where(
                ManagerSession.account_id == account.id,
                ManagerSession.token_hash != session_digest,
                ManagerSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal,
        action=f"account.{action}",
        entity_type="manager_account",
        entity_id=account.id,
        correlation_id=uuid.uuid4(),
        reason=None,
        redacted_changes={"mfa_enabled": account.mfa_secret is not None},
        request_id=request_id,
    )
    if action == "confirm_mfa":
        queue_security_alert(session, account.id, account.workspace_id, "mfa_enabled", settings, clock)
    elif action == "disable_mfa":
        queue_security_alert(session, account.id, account.workspace_id, "mfa_disabled", settings, clock)
    snapshot = {
        "body": result,
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "state": hashlib.sha256((account.mfa_secret or b"") + (account.mfa_pending_secret or b"")).hexdigest(),
    }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=principal,
        route=route,
        key=key,
        digest=digest,
        status=200,
        body={"encrypted_response": protect_text(json.dumps(snapshot), secret).hex()},
        resource_id=account.id,
    )
    return result
