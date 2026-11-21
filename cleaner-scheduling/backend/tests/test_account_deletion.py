"""M4 Account deletion service tests.

Tests for backend/app/services/account_deletion.py covering:
- Password verification required
- TOTP required when MFA enabled
- Sessions revoked, workspace tombstoned
- WorkspaceConnection token wiped, completed_at cleared
- Cleaner anonymization (display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac)
- Access links revoked
- Outbox cleared (payload={}, processed_at set, claimed_until cleared, error_code=account_deleted)
- EmailDeliveries snapshot_ciphertext cleared, queued->stale, error_code=account_deleted
- 24h idempotent replay (same key succeeds, different key or >24h gives invalid_session)
- account_busy on lock timeout (real concurrent lock test)

Production pattern (accounts.py:97-111, 253-262): catalog_session_factory is used for account
routes, and authenticate_session sets the workspace_id mid-transaction. Seeding uses migrator.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from app.clock import FrozenClock
from app.config import Settings
from app.db import catalog_session_factory, get_engine
from app.domain.errors import AppError
from app.models import Cleaner, CleanerAccessLink, OperatorProfile, OutboxEvent
from app.models.accounts import ManagerAccount, ManagerSession
from app.models.tables import EmailDelivery
from app.models.workspace_connection import WorkspaceConnection
from app.services.account_deletion import delete_account
from app.services.field_cipher import protect_text
from app.services.manager_mfa import change_mfa
from app.services.workspace_accounts import account_subject, password_hash, token_digest
from app.workspaces import Workspace
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    pass


def _migrator_session() -> sessionmaker[Session]:
    """Create session factory using migrator credentials (bypasses RLS)."""
    return sessionmaker(bind=get_engine(os.environ["MIGRATION_DATABASE_URL"]), expire_on_commit=False)


def _create_test_workspace(settings: Settings, clock: FrozenClock) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    """Create workspace with account, session, connection (bypasses RLS).

    Returns (workspace_id, account_id, session_token, session_digest).
    """
    factory = _migrator_session()

    workspace_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session_token = f"mgr_{secrets.token_urlsafe(43)}"
    session_digest = token_digest(session_token)

    with factory() as s:
        workspace = Workspace(id=workspace_id, name="Deletion Test Workspace", created_at=clock.now())
        s.add(workspace)
        s.flush()

        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        signup_key = secrets.token_urlsafe(32)
        account = ManagerAccount(
            id=account_id,
            workspace_id=workspace_id,
            email=f"deletion-test-{account_id}@example.com",
            password_hash=password_hash("TestPassword123!"),
            signup_key_hash=token_digest(signup_key),
            signup_request_hash="",
            created_at=clock.now() - timedelta(days=1),
            mfa_secret=None,
        )
        s.add(account)
        s.flush()

        operator = OperatorProfile(
            id=uuid.uuid4(),
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            auth_subject=account_subject(account),
            display_name="Test Owner",
            roles=["owner", "manager"],
            active=True,
        )
        s.add(operator)

        session_obj = ManagerSession(
            token_hash=session_digest,
            account_id=account_id,
            created_at=clock.now(),
            expires_at=clock.now() + timedelta(hours=24),
            revoked_at=None,
        )
        s.add(session_obj)

        connection = WorkspaceConnection(
            workspace_id=workspace_id,
            token_ciphertext=protect_text("test-token", settings.field_encryption_secret),
            connected_at=clock.now(),
            completed_at=clock.now(),
        )
        s.add(connection)

        s.commit()

    return workspace_id, account_id, session_token, session_digest


def _add_cleaner_with_link(
    settings: Settings, clock: FrozenClock, workspace_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Add a cleaner with access link and full PII fields."""
    factory = _migrator_session()
    cleaner_id = uuid.uuid4()
    link_id = uuid.uuid4()

    with factory() as s:
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        cleaner = Cleaner(
            id=cleaner_id,
            display_name="Test Cleaner",
            phone_ciphertext=protect_text("+15551234567", settings.field_encryption_secret),
            email_ciphertext=protect_text("cleaner@example.com", settings.field_encryption_secret),
            phone_lookup_hmac="test-phone-hmac-value",
            sms_consent_status="opted_in",
            active=True,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            default_work_intervals=[],
        )
        s.add(cleaner)
        s.flush()

        link = CleanerAccessLink(
            id=link_id,
            cleaner_id=cleaner_id,
            token_hash=secrets.token_hex(32),
            created_at=clock.now(),
            expires_at=clock.now() + timedelta(days=30),
            revoked_at=None,
        )
        s.add(link)
        s.commit()

    return cleaner_id, link_id


def _add_outbox_event(clock: FrozenClock, workspace_id: uuid.UUID) -> uuid.UUID:
    """Add an outbox event with payload."""
    factory = _migrator_session()
    event_id = uuid.uuid4()

    with factory() as s:
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        event = OutboxEvent(
            id=event_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            topic="test.event",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={"test": "data"},
            available_at=clock.now(),
            attempt_count=0,
            claimed_until=clock.now() + timedelta(minutes=5),
        )
        s.add(event)
        s.commit()

    return event_id


def _add_email_delivery(
    settings: Settings,
    clock: FrozenClock,
    workspace_id: uuid.UUID,
    cleaner_id: uuid.UUID,
    link_id: uuid.UUID,
    status: str,
) -> uuid.UUID:
    """Add an email delivery record (onboarding kind)."""
    factory = _migrator_session()
    delivery_id = uuid.uuid4()

    with factory() as s:
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            kind="onboarding",
            onboarding_grant_id=link_id,
            service_date=clock.now().date() + timedelta(days=1),
            message_key=f"test-delivery:{delivery_id}",
            fingerprint="test-fingerprint",
            snapshot_ciphertext=protect_text('{"recipient": "test@example.com"}', settings.field_encryption_secret),
            status=status,
            attempt_count=0,
            next_attempt_at=clock.now(),
        )
        s.add(delivery)
        s.commit()

    return delivery_id


def _call_delete_account(
    settings: Settings, clock: FrozenClock, token: str, password: str, second_factor: str, key: str
) -> dict[str, object]:
    """Call delete_account using catalog_session_factory (matches production pattern)."""
    catalog = catalog_session_factory(settings)
    with catalog() as s:
        return delete_account(
            s,
            token=token,
            password=password,
            second_factor=second_factor,
            key=key,
            settings=settings,
            clock=clock,
            request_id=None,
        )


def _readback(workspace_id: uuid.UUID) -> Session:
    """Create a migrator session with workspace context for readback."""
    factory = _migrator_session()
    s = factory()
    s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})
    return s


class TestAccountDeletionValidation:
    """Tests for account deletion validation."""

    def test_wrong_password_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Wrong password is rejected.

        Mutation: Remove verify_password(password, account.password_hash) in account_deletion.py:62.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        with pytest.raises(AppError) as exc_info:
            _call_delete_account(settings, clock, token, "WrongPassword123!", "", "test-key-1")
        assert exc_info.value.code == "invalid_credentials"

    def test_totp_required_when_mfa_enabled(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """TOTP is required when MFA is enabled.

        Mutation: Remove consume_second_factor call in account_deletion.py:65.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        catalog = catalog_session_factory(settings)
        with catalog() as s:
            from app.services.workspace_accounts import authenticate_session

            authenticate_session(s, token, clock.now())
            acct = s.get(ManagerAccount, account_id)
            assert acct is not None

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-del-1",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with catalog() as s:
            from app.services.workspace_accounts import authenticate_session

            authenticate_session(s, token, clock.now())
            acct = s.get(ManagerAccount, account_id)
            assert acct is not None

            current = int(clock.now().timestamp()) // 30
            key = base64.b32decode(secret)
            digest = hmac.new(key, current.to_bytes(8, "big"), hashlib.sha1).digest()
            offset = digest[-1] & 15
            code = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1000000
            totp_code = f"{code:06d}"

            change_mfa(
                s,
                account=acct,
                action="confirm_mfa",
                password="TestPassword123!",
                second_factor=totp_code,
                session_digest=session_digest,
                key="confirm-key-del-1",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            s.commit()

        with pytest.raises(AppError) as exc_info:
            _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-1")
        assert exc_info.value.code == "mfa_required"


class TestAccountDeletionEffects:
    """Tests for account deletion side effects."""

    def test_sessions_revoked(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """All sessions are revoked after deletion.

        Mutation: Remove UPDATE manager_sessions SET revoked_at in account_deletion.py:91.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-sess")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            mgr_session = s.scalar(select(ManagerSession).where(ManagerSession.account_id == account_id))
            assert mgr_session is not None
            assert mgr_session.revoked_at is not None

    def test_workspace_tombstoned(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Workspace is tombstoned after deletion.

        Mutation: Remove UPDATE workspaces SET deleted_at in account_deletion.py:130-132.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-ws")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            ws = s.get(Workspace, workspace_id)
            assert ws is not None
            assert ws.deleted_at is not None

    def test_hospitable_token_wiped(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Hospitable token is wiped and completed_at cleared after deletion.

        Mutation: Remove UPDATE workspace_connections SET token_ciphertext = '' in account_deletion.py:94-95.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-token")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            conn = s.get(WorkspaceConnection, workspace_id)
            assert conn is not None
            assert conn.token_ciphertext == b""
            assert conn.completed_at is None

    def test_cleaners_fully_anonymized(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Cleaners are fully anonymized: display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac wiped.

        Mutation: Remove UPDATE cleaners SET display_name = 'Deleted cleaner', phone_ciphertext = ''
        in account_deletion.py:98-100.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)
        cleaner_id, link_id = _add_cleaner_with_link(settings, clock, workspace_id)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-cleaner")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            cleaner = s.get(Cleaner, cleaner_id)
            assert cleaner is not None
            assert cleaner.display_name == "Deleted cleaner"
            assert cleaner.active is False
            assert cleaner.phone_ciphertext == b""
            assert cleaner.email_ciphertext is None
            assert cleaner.phone_lookup_hmac == str(cleaner_id)

    def test_access_links_revoked(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Access links are revoked after deletion.

        Mutation: Remove UPDATE cleaner_access_links SET revoked_at in account_deletion.py:101.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)
        cleaner_id, link_id = _add_cleaner_with_link(settings, clock, workspace_id)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-link")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            link = s.get(CleanerAccessLink, link_id)
            assert link is not None
            assert link.revoked_at is not None

    def test_outbox_events_cleared(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Outbox events: payload={}, processed_at set, claimed_until cleared, error_code=account_deleted.

        Mutation: Remove UPDATE outbox_events SET payload = '{}' in account_deletion.py:109-111.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)
        event_id = _add_outbox_event(clock, workspace_id)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-outbox")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            event = s.get(OutboxEvent, event_id)
            assert event is not None
            assert event.payload == {}
            assert event.processed_at is not None
            assert event.claimed_until is None
            assert event.last_error_code == "account_deleted"

    def test_email_deliveries_cleared(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """EmailDeliveries: snapshot_ciphertext='', queued->stale, error_code=account_deleted.

        Mutation: Remove UPDATE email_deliveries SET snapshot_ciphertext = '' in account_deletion.py:106-108.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)
        cleaner_id, link_id = _add_cleaner_with_link(settings, clock, workspace_id)
        queued_id = _add_email_delivery(settings, clock, workspace_id, cleaner_id, link_id, "queued")
        sending_id = _add_email_delivery(settings, clock, workspace_id, cleaner_id, link_id, "sending")
        accepted_id = _add_email_delivery(settings, clock, workspace_id, cleaner_id, link_id, "accepted")

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-email")
        assert result["deleted"] is True

        with _readback(workspace_id) as s:
            queued = s.get(EmailDelivery, queued_id)
            assert queued is not None
            assert queued.snapshot_ciphertext == b""
            assert queued.status == "stale"
            assert queued.error_code == "account_deleted"

            sending = s.get(EmailDelivery, sending_id)
            assert sending is not None
            assert sending.snapshot_ciphertext == b""
            assert sending.status == "stale"
            assert sending.error_code == "account_deleted"

            accepted = s.get(EmailDelivery, accepted_id)
            assert accepted is not None
            assert accepted.snapshot_ciphertext == b""
            assert accepted.status == "accepted"
            assert accepted.error_code == "account_deleted"


class TestAccountDeletionReplay:
    """Tests for 24h idempotent replay."""

    def test_24h_idempotent_replay_same_key(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Deletion within 24h with same credentials and key returns success.

        Mutation: Remove deleted_at + 24h replay check in account_deletion.py:53-59.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-replay")
        assert result["deleted"] is True

        later_clock = FrozenClock(clock.now() + timedelta(hours=12))
        result = _call_delete_account(settings, later_clock, token, "TestPassword123!", "", "del-key-replay")
        assert result["deleted"] is True

    def test_replay_different_key_gives_invalid_session(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Deletion with different key after first deletion gives invalid_session.

        Mutation: Accept any key in replay check at account_deletion.py:56.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-first")
        assert result["deleted"] is True

        later_clock = FrozenClock(clock.now() + timedelta(hours=1))
        with pytest.raises(AppError) as exc_info:
            _call_delete_account(settings, later_clock, token, "TestPassword123!", "", "del-key-different")
        assert exc_info.value.code == "invalid_session"

    def test_replay_after_24h_gives_invalid_session(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Deletion more than 24h after first deletion gives invalid_session.

        Mutation: Extend the 24h window in account_deletion.py:54.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        result = _call_delete_account(settings, clock, token, "TestPassword123!", "", "del-key-replay")
        assert result["deleted"] is True

        later_clock = FrozenClock(clock.now() + timedelta(hours=25))
        with pytest.raises(AppError) as exc_info:
            _call_delete_account(settings, later_clock, token, "TestPassword123!", "", "del-key-replay")
        assert exc_info.value.code == "invalid_session"


class TestAccountDeletionLocking:
    """Tests for account_busy locking via real concurrent lock."""

    def test_account_busy_on_concurrent_lock(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """delete_account returns account_busy/503 when workspace row is locked.

        Session A holds SELECT ... FROM workspaces WHERE id=... FOR UPDATE open.
        Session B sets a short lock_timeout and calls delete_account.
        Assert account_busy/503 and that nothing changed.

        Mutation: Drop 55P03 from the sqlstate set at account_deletion.py:72.
        """
        workspace_id, account_id, token, session_digest = _create_test_workspace(settings, clock)

        barrier = threading.Barrier(2, timeout=10)
        lock_acquired = threading.Event()
        result_holder: list[AppError] = []
        lock_released = threading.Event()

        def hold_lock() -> None:
            factory = _migrator_session()
            with factory() as s:
                s.execute(text("SELECT id FROM workspaces WHERE id = :id FOR UPDATE"), {"id": workspace_id})
                lock_acquired.set()
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                lock_released.wait(timeout=5)

        lock_thread = threading.Thread(target=hold_lock)
        lock_thread.start()

        try:
            lock_acquired.wait(timeout=5)
            assert lock_acquired.is_set(), "Lock thread should have acquired lock"

            catalog = catalog_session_factory(settings)
            with catalog() as s:
                s.execute(text("SET LOCAL lock_timeout = '100ms'"))
                try:
                    delete_account(
                        s,
                        token=token,
                        password="TestPassword123!",
                        second_factor="",
                        key="del-key-lock",
                        settings=settings,
                        clock=clock,
                        request_id=None,
                    )
                    pytest.fail("Should have raised AppError")
                except AppError as e:
                    result_holder.append(e)

            barrier.wait()
        finally:
            barrier.abort()
            lock_released.set()
            lock_thread.join(timeout=5)
            assert not lock_thread.is_alive(), "Lock thread should have exited"

        assert len(result_holder) == 1
        delete_error = result_holder[0]
        assert delete_error.code == "account_busy"
        assert delete_error.status_code == 503

        with _readback(workspace_id) as s:
            ws = s.get(Workspace, workspace_id)
            assert ws is not None
            assert ws.deleted_at is None, "Workspace should not be deleted after lock timeout"

            mgr_session = s.scalar(select(ManagerSession).where(ManagerSession.account_id == account_id))
            assert mgr_session is not None
            assert mgr_session.revoked_at is None, "Session should not be revoked after lock timeout"
