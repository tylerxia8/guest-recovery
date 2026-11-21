from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models.accounts import ManagerAccount, ManagerSession
from app.services.audit import write_audit
from app.services.manager_mfa import consume_second_factor
from app.services.workspace_accounts import authenticate_session, token_digest, verify_password

logger = logging.getLogger(__name__)


def delete_account(
    session: Session,
    *,
    token: str,
    password: str,
    second_factor: str,
    key: str,
    settings: Settings,
    clock: Clock,
    request_id: str | None,
) -> dict[str, object]:
    now = clock.now()
    session_hash = token_digest(token)
    key_hash = token_digest(key)
    digest = hmac.new(
        settings.field_encryption_secret.encode(),
        json.dumps(["delete_account", password, second_factor, session_hash]).encode(),
        hashlib.sha256,
    ).hexdigest()
    account = session.scalar(
        select(ManagerAccount)
        .join(ManagerSession, ManagerSession.account_id == ManagerAccount.id)
        .where(ManagerSession.token_hash == session_hash)
        .with_for_update(of=ManagerAccount)
    )
    if account is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    if account.deleted_at is not None:
        if (
            account.deleted_at + timedelta(hours=24) > now
            and account.deletion_session_hash == session_hash
            and account.deletion_key_hash == key_hash
            and hmac.compare_digest(account.deletion_request_hash or "", digest)
        ):
            return {"deleted": True}
        raise AppError("invalid_session", "Sign in to continue.", 401)
    authenticate_session(session, token, now)
    if not verify_password(password, account.password_hash):
        raise AppError("invalid_credentials", "Password is incorrect.", 401)
    if account.mfa_secret is not None:
        consume_second_factor(account, second_factor, settings.field_encryption_secret, now)
    try:
        active = session.scalar(
            text("SELECT deleted_at IS NULL FROM workspaces WHERE id = :id FOR UPDATE"),
            {"id": account.workspace_id},
        )
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) not in {"55P03", "57014", "40P01"}:
            raise
        session.rollback()
        raise AppError("account_busy", "Account activity is finishing. Try deleting again shortly.", 503) from error
    if active is not True:
        raise AppError("account_deleted", "This workspace is no longer available.", 401)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=f"manager:{account.id}",
        action="account.deleted",
        entity_type="manager_account",
        entity_id=account.id,
        correlation_id=uuid.uuid4(),
        reason=None,
        redacted_changes={"sessions_revoked": True, "communications_disabled": True},
        request_id=request_id,
    )
    session.execute(update(ManagerSession).where(ManagerSession.account_id == account.id).values(revoked_at=now))
    parameters = {"workspace": account.workspace_id, "now": now}
    statements = (
        "UPDATE workspace_connections SET token_ciphertext = ''::bytea, completed_at = NULL "
        "WHERE workspace_id = :workspace",
        "UPDATE operator_profiles SET display_name = 'Deleted account', active = false, "
        "version = version + 1 WHERE workspace_id = :workspace",
        "UPDATE cleaners SET display_name = 'Deleted cleaner', active = false, phone_ciphertext = ''::bytea, "
        "email_ciphertext = NULL, phone_lookup_hmac = id::text, consent_evidence_ref = NULL, "
        "version = version + 1 WHERE workspace_id = :workspace",
        "UPDATE cleaner_access_links SET revoked_at = :now WHERE workspace_id = :workspace",
        "UPDATE cleaner_weekly_hours SET starting_address_ciphertext = NULL, origin_routes = NULL, "
        "version = version + 1 WHERE workspace_id = :workspace",
        "UPDATE cleaner_daily_availability SET origin_label = NULL, origin_ciphertext = NULL, "
        "origin_source = NULL, version = version + 1 WHERE workspace_id = :workspace",
        "UPDATE email_deliveries SET snapshot_ciphertext = ''::bytea, "
        "status = CASE WHEN status IN ('queued', 'sending') THEN 'stale' ELSE status END, "
        "error_code = 'account_deleted', updated_at = :now, version = version + 1 WHERE workspace_id = :workspace",
        "UPDATE outbox_events SET payload = '{}'::jsonb, processed_at = coalesce(processed_at, :now), "
        "claimed_until = NULL, last_error_code = 'account_deleted', version = version + 1 "
        "WHERE workspace_id = :workspace",
        "DELETE FROM idempotency_records WHERE workspace_id = :workspace",
    )
    for statement in statements:
        session.execute(text(statement), parameters)
    account.email = f"deleted-{account.id}@deleted.invalid"
    account.password_hash = "scrypt$" + "00" * 16 + "$" + "00" * 32
    account.signup_request_hash = ""
    account.mfa_secret = None
    account.mfa_pending_secret = None
    account.mfa_pending_session_hash = None
    account.mfa_pending_expires_at = None
    account.mfa_last_counter = None
    account.mfa_recovery_hashes = []
    account.deletion_key_hash = key_hash
    account.deletion_request_hash = digest
    account.deletion_session_hash = session_hash
    account.deleted_at = now
    session.flush()
    session.execute(
        text("UPDATE workspaces SET name = 'Deleted workspace', deleted_at = :now WHERE id = :workspace"),
        parameters,
    )
    session.commit()
    logger.info("account.deleted", extra={"workspace_id": str(account.workspace_id), "account_id": str(account.id)})
    return {"deleted": True}
