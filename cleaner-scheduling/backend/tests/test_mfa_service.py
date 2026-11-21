"""M4 MFA service tests.

Tests for backend/app/services/manager_mfa.py covering:
- Enroll MFA through the app's service
- Verify correct code through the app's service
- Reject wrong code
- Reject reuse of same counter
- Recovery code single-use

NOTE: MFA security alert tests are in test_security_alerts.py::test_mfa_confirm_queues_security_alert
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from app.clock import FrozenClock
from app.config import Settings
from app.db import session_factory
from app.domain.errors import AppError
from app.models.accounts import ManagerAccount, ManagerSession
from app.services.workspace_accounts import password_hash, token_digest
from app.workspaces import LEGACY_WORKSPACE_ID, Workspace

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _get_account(s: Session, account_id: uuid.UUID) -> ManagerAccount:
    """Get account with type assertion."""
    acct = s.get(ManagerAccount, account_id)
    assert acct is not None
    return acct


def _create_workspace_and_account(
    session: Session, settings: Settings, clock: FrozenClock, *, with_mfa: bool = False
) -> tuple[uuid.UUID, ManagerAccount, str, str]:
    """Create a test workspace and account.

    Returns (workspace_id, account, session_token, session_digest).
    """
    workspace_id = uuid.uuid4()
    workspace = Workspace(id=workspace_id, name="MFA Test Workspace", created_at=clock.now())
    session.add(workspace)
    session.flush()

    account_id = uuid.uuid4()
    signup_key = secrets.token_urlsafe(32)
    account = ManagerAccount(
        id=account_id,
        workspace_id=workspace_id,
        email=f"mfa-test-{account_id}@example.com",
        password_hash=password_hash("TestPassword123!"),
        signup_key_hash=token_digest(signup_key),
        signup_request_hash="",
        created_at=clock.now() - timedelta(days=1),
        mfa_secret=None,
    )
    session.add(account)
    session.flush()

    session_token = f"mgr_{secrets.token_urlsafe(43)}"
    session_digest = token_digest(session_token)
    session_obj = ManagerSession(
        token_hash=session_digest,
        account_id=account_id,
        created_at=clock.now(),
        expires_at=clock.now() + timedelta(hours=24),
        revoked_at=None,
    )
    session.add(session_obj)
    session.commit()

    return workspace_id, account, session_token, session_digest


class TestMfaService:
    """Tests for manager_mfa service using the app's enroll/verify flow."""

    def test_enroll_creates_pending_secret(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """MFA enrollment creates a pending secret for verification.

        Mutation: Don't set mfa_pending_secret in begin_mfa action.
        """
        from app.services.manager_mfa import change_mfa

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            workspace_id, account, token, session_digest = _create_workspace_and_account(s, settings, clock)
            account_id = account.id

        factory = session_factory(settings, workspace_id)
        with factory() as s:
            acct = _get_account(s, account_id)

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-1",
                settings=settings,
                clock=clock,
                request_id=None,
            )

            assert result["mfa_enabled"] is False
            assert "secret" in result
            secret = result["secret"]
            assert isinstance(secret, str) and len(secret) == 32
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            assert acct.mfa_pending_secret is not None

    def test_verify_correct_code_enables_mfa(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Verifying correct code enables MFA and returns recovery codes.

        Mutation: Don't set mfa_secret on confirm at manager_mfa.py.
        """
        import base64
        import hashlib
        import hmac

        from app.services.manager_mfa import change_mfa

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            workspace_id, account, token, session_digest = _create_workspace_and_account(s, settings, clock)
            account_id = account.id

        factory = session_factory(settings, workspace_id)
        with factory() as s:
            acct = _get_account(s, account_id)

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-2",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            current = int(clock.now().timestamp()) // 30
            key = base64.b32decode(secret)
            digest = hmac.new(key, current.to_bytes(8, "big"), hashlib.sha1).digest()
            offset = digest[-1] & 15
            code = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000
            totp_code = f"{code:06d}"

            result = change_mfa(
                s,
                account=acct,
                action="confirm_mfa",
                password="TestPassword123!",
                second_factor=totp_code,
                session_digest=session_digest,
                key="confirm-key-2",
                settings=settings,
                clock=clock,
                request_id=None,
            )

            assert result["mfa_enabled"] is True
            assert "recovery_codes" in result
            recovery_codes = result["recovery_codes"]
            assert isinstance(recovery_codes, list) and len(recovery_codes) == 8
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)
            assert acct.mfa_secret is not None

    def test_wrong_code_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Wrong TOTP code is rejected during confirmation.

        Mutation: Accept any code in confirm_mfa action.
        """
        import base64
        import hashlib
        import hmac

        from app.services.manager_mfa import change_mfa

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            workspace_id, account, token, session_digest = _create_workspace_and_account(s, settings, clock)
            account_id = account.id

        factory = session_factory(settings, workspace_id)
        with factory() as s:
            acct = _get_account(s, account_id)

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-3",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            current = int(clock.now().timestamp()) // 30
            key = base64.b32decode(secret)

            def code_for_counter(counter: int) -> int:
                digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
                offset = digest[-1] & 15
                return (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000

            window_codes = {code_for_counter(c) for c in (current - 1, current, current + 1)}
            wrong_code_int = next(c for c in range(1000000) if c not in window_codes)
            wrong_code = f"{wrong_code_int:06d}"

            with pytest.raises(AppError) as exc_info:
                change_mfa(
                    s,
                    account=acct,
                    action="confirm_mfa",
                    password="TestPassword123!",
                    second_factor=wrong_code,
                    session_digest=session_digest,
                    key="confirm-key-3",
                    settings=settings,
                    clock=clock,
                    request_id=None,
                )
            assert exc_info.value.code == "invalid_second_factor"

    def test_counter_reuse_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Reusing the same TOTP counter is rejected.

        Mutation: Don't check last_counter in totp_counter.
        """
        import base64
        import hashlib
        import hmac

        from app.services.manager_mfa import change_mfa, consume_second_factor

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            workspace_id, account, token, session_digest = _create_workspace_and_account(s, settings, clock)
            account_id = account.id

        factory = session_factory(settings, workspace_id)

        with factory() as s:
            acct = _get_account(s, account_id)

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-4",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            current = int(clock.now().timestamp()) // 30
            key = base64.b32decode(secret)
            digest = hmac.new(key, current.to_bytes(8, "big"), hashlib.sha1).digest()
            offset = digest[-1] & 15
            code = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000
            totp_code = f"{code:06d}"

            result = change_mfa(
                s,
                account=acct,
                action="confirm_mfa",
                password="TestPassword123!",
                second_factor=totp_code,
                session_digest=session_digest,
                key="confirm-key-4",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            assert result["mfa_enabled"] is True
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)
            assert acct.mfa_secret is not None

            with pytest.raises(AppError) as exc_info:
                consume_second_factor(acct, totp_code, settings.field_encryption_secret, clock.now())
            assert exc_info.value.code == "invalid_second_factor"

    def test_recovery_code_single_use(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Recovery codes can only be used once.

        Mutation: Don't remove used recovery code from list.
        """
        import base64
        import hashlib
        import hmac

        from app.services.manager_mfa import change_mfa, consume_second_factor

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            workspace_id, account, token, session_digest = _create_workspace_and_account(s, settings, clock)
            account_id = account.id

        factory = session_factory(settings, workspace_id)

        with factory() as s:
            acct = _get_account(s, account_id)

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-5",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            current = int(clock.now().timestamp()) // 30
            key = base64.b32decode(secret)
            digest = hmac.new(key, current.to_bytes(8, "big"), hashlib.sha1).digest()
            offset = digest[-1] & 15
            code = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000
            totp_code = f"{code:06d}"

            result = change_mfa(
                s,
                account=acct,
                action="confirm_mfa",
                password="TestPassword123!",
                second_factor=totp_code,
                session_digest=session_digest,
                key="confirm-key-5",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            recovery_codes = result["recovery_codes"]
            assert isinstance(recovery_codes, list)
            s.commit()

        later_clock = FrozenClock(clock.now() + timedelta(seconds=31))

        with factory() as s:
            acct = _get_account(s, account_id)
            first_code = recovery_codes[0]
            assert isinstance(first_code, str)
            recovery_code = first_code.replace("-", "")
            consume_second_factor(acct, recovery_code, settings.field_encryption_secret, later_clock.now())
            s.commit()

        with factory() as s:
            acct = _get_account(s, account_id)

            with pytest.raises(AppError) as exc_info:
                consume_second_factor(acct, recovery_code, settings.field_encryption_secret, later_clock.now())
            assert exc_info.value.code == "invalid_second_factor"
