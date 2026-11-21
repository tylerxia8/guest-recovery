"""M4 Security alerts service tests.

Tests for backend/app/services/security_alerts.py covering:
- Real event triggers create outbox row with correct topic and recipient
- Deduped on replay
- Disabled when security_alert_email_enabled=False (even with hosted_workspace=True)
- MFA confirm triggers alert and snapshot contains recipient and subject
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import PropertyMock, patch

from app.clock import FrozenClock
from app.config import Settings
from app.db import get_engine, session_factory
from app.models import OutboxEvent
from app.services.field_cipher import reveal_text
from app.workspaces import Workspace
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    pass


def _migrator_session() -> sessionmaker[Session]:
    """Create session factory using migrator credentials (bypasses RLS)."""
    return sessionmaker(bind=get_engine(os.environ["MIGRATION_DATABASE_URL"]), expire_on_commit=False)


class TestSecurityAlerts:
    """Tests for security_alerts service with real event triggers."""

    def test_queue_creates_outbox_with_topic_recipient_and_subject(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """queue_security_alert creates outbox row; snapshot decrypts to correct recipient and subject.

        Mutation: Don't insert OutboxEvent in queue_security_alert at security_alerts.py:63.
        """
        from app.services.security_alerts import queue_security_alert

        migrator = _migrator_session()
        workspace_id = uuid.uuid4()
        account_id = uuid.uuid4()

        with migrator() as s:
            workspace = Workspace(id=workspace_id, name="Alert Test Workspace", created_at=clock.now())
            s.add(workspace)
            s.commit()

        factory = session_factory(settings, workspace_id)
        enabled_settings = settings.model_copy(
            update={"security_alert_email_enabled": True, "security_alert_email": "alerts@example.com"}
        )

        with patch.object(Settings, "hosted_workspace", new_callable=PropertyMock, return_value=True):
            with factory() as s:
                queue_security_alert(s, account_id, workspace_id, "mfa_enabled", enabled_settings, clock)
                s.commit()

        with factory() as s:
            event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "security.email_alert",
                    OutboxEvent.aggregate_id == account_id,
                )
            )
            assert event is not None, "Security alert should be created"
            assert event.payload.get("kind") == "mfa_enabled"
            assert "snapshot" in event.payload

            snapshot_hex = event.payload["snapshot"]
            assert isinstance(snapshot_hex, str)
            snapshot_bytes = bytes.fromhex(snapshot_hex)
            snapshot_json = reveal_text(snapshot_bytes, settings.field_encryption_secret)
            snapshot = json.loads(snapshot_json)

            assert snapshot["recipient"] == "alerts@example.com"
            assert "Two-factor authentication enabled" in snapshot["subject"]

    def test_alert_deduped_on_replay(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Security alerts are deduped within the same hour.

        Mutation: Remove on_conflict_do_nothing at security_alerts.py:81.
        Fails: IntegrityError raised on duplicate insert.
        """
        from app.services.security_alerts import queue_security_alert

        migrator = _migrator_session()
        workspace_id = uuid.uuid4()
        account_id = uuid.uuid4()

        with migrator() as s:
            workspace = Workspace(id=workspace_id, name="Dedupe Test Workspace", created_at=clock.now())
            s.add(workspace)
            s.commit()

        factory = session_factory(settings, workspace_id)
        enabled_settings = settings.model_copy(
            update={"security_alert_email_enabled": True, "security_alert_email": "alerts@example.com"}
        )

        with patch.object(Settings, "hosted_workspace", new_callable=PropertyMock, return_value=True):
            with factory() as s:
                queue_security_alert(s, account_id, workspace_id, "mfa_enabled", enabled_settings, clock)
                s.commit()

            with factory() as s:
                queue_security_alert(s, account_id, workspace_id, "mfa_enabled", enabled_settings, clock)
                s.commit()

        with factory() as s:
            events = list(
                s.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.topic == "security.email_alert",
                        OutboxEvent.aggregate_id == account_id,
                        OutboxEvent.payload["kind"].as_string() == "mfa_enabled",
                    )
                )
            )
            assert len(events) == 1, "Should only have one event (deduped)"

    def test_no_alert_when_email_disabled_but_hosted(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Security alerts are skipped when security_alert_email_enabled=False, even if hosted_workspace=True.

        This tests the enabled check at security_alerts.py:40, not the hosted_workspace early return.

        Mutation: Remove security_alert_email_enabled check at security_alerts.py:40.
        """
        from app.services.security_alerts import queue_security_alert

        migrator = _migrator_session()
        workspace_id = uuid.uuid4()
        account_id = uuid.uuid4()

        with migrator() as s:
            workspace = Workspace(id=workspace_id, name="Disabled Alert Test", created_at=clock.now())
            s.add(workspace)
            s.commit()

        factory = session_factory(settings, workspace_id)
        disabled_settings = settings.model_copy(
            update={"security_alert_email_enabled": False, "security_alert_email": "alerts@example.com"}
        )

        with patch.object(Settings, "hosted_workspace", new_callable=PropertyMock, return_value=True):
            with factory() as s:
                queue_security_alert(s, account_id, workspace_id, "repeated_failed_login", disabled_settings, clock)
                s.commit()

        with factory() as s:
            event = s.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == account_id))
            assert event is None, "Alert should not be created when security_alert_email_enabled=False"

    def test_mfa_confirm_queues_security_alert_with_snapshot(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """MFA confirmation triggers security alert via change_mfa; snapshot decrypts to correct recipient/subject.

        Mutation: Remove queue_security_alert call in manager_mfa.py confirm_mfa branch.
        """
        import base64
        import hashlib
        import hmac
        import secrets

        from app.models import OperatorProfile
        from app.models.accounts import ManagerAccount, ManagerSession
        from app.services.manager_mfa import change_mfa
        from app.services.workspace_accounts import account_subject, password_hash, token_digest

        migrator = _migrator_session()
        workspace_id = uuid.uuid4()
        account_id = uuid.uuid4()
        session_token = f"mgr_{secrets.token_urlsafe(43)}"
        session_digest = token_digest(session_token)

        with migrator() as s:
            workspace = Workspace(id=workspace_id, name="MFA Alert Test", created_at=clock.now())
            s.add(workspace)
            s.flush()

            s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

            signup_key = secrets.token_urlsafe(32)
            account = ManagerAccount(
                id=account_id,
                workspace_id=workspace_id,
                email=f"mfa-alert-test-{account_id}@example.com",
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
            s.commit()

        factory = session_factory(settings, workspace_id)
        enabled_settings = settings.model_copy(
            update={"security_alert_email_enabled": True, "security_alert_email": "security-team@example.com"}
        )

        with factory() as s:
            acct = s.get(ManagerAccount, account_id)
            assert acct is not None

            result = change_mfa(
                s,
                account=acct,
                action="begin_mfa",
                password="TestPassword123!",
                second_factor="",
                session_digest=session_digest,
                key="enroll-key-alert-1",
                settings=settings,
                clock=clock,
                request_id=None,
            )
            secret = result["secret"]
            assert isinstance(secret, str)
            s.commit()

        with patch.object(Settings, "hosted_workspace", new_callable=PropertyMock, return_value=True):
            with factory() as s:
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
                    key="confirm-key-alert-1",
                    settings=enabled_settings,
                    clock=clock,
                    request_id=None,
                )
                s.commit()

        with factory() as s:
            event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "security.email_alert",
                    OutboxEvent.aggregate_id == account_id,
                    OutboxEvent.payload["kind"].as_string() == "mfa_enabled",
                )
            )
            assert event is not None, "Security alert should be queued when MFA enabled"

            snapshot_hex = event.payload["snapshot"]
            assert isinstance(snapshot_hex, str)
            snapshot_bytes = bytes.fromhex(snapshot_hex)
            snapshot_json = reveal_text(snapshot_bytes, settings.field_encryption_secret)
            snapshot = json.loads(snapshot_json)

            assert snapshot["recipient"] == "security-team@example.com"
            assert "Two-factor authentication enabled" in snapshot["subject"]
