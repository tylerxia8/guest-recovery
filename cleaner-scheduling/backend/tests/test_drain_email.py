"""Tests for email_automation._drain behavior.

These tests verify error handling paths in _drain:
- Send error → uncertain with submitting_at marker set
- Connect error → queued (retryable)
- KeyError → failed with invalid_snapshot
- Stranded without marker → queued
- Stranded with marker → uncertain
- Row re-claimed before sending → not sent

Each test MUST fail on PR #65 head; paste the failure.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from app.api.routes.onboarding import onboarding_token
from app.config import get_settings
from app.db import session_factory
from app.models import (
    Cleaner,
    CleanerAccessLink,
    CleanerWeeklyHours,
    EmailDelivery,
    OperationalException,
    Property,
)
from app.services.email_automation import _drain
from app.services.field_cipher import protect_text
from app.workspaces import LEGACY_WORKSPACE_ID
from sqlalchemy import select

if TYPE_CHECKING:
    from app.clock import Clock
    from sqlalchemy.orm import Session


class TestDrainSendError:
    """Test that send errors result in uncertain with submitting_at set."""

    def test_send_error_marks_uncertain_with_marker(self, session: Session, clock: Clock) -> None:
        """A send error after submitting_at is set should mark row uncertain.

        MUST fail on PR #65 head.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None, "Need seeded property"

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Send Error",
            phone_ciphertext=protect_text("+15551234567", settings.field_encryption_secret),
            email_ciphertext=protect_text("test@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-send-error"
        snapshot = {
            "recipient": "test@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-send-error-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=0,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview(*args: object, **kwargs: object) -> dict[str, object]:
            return {
                "recipient": "test@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
                "fingerprint": test_fingerprint,
            }

        def mock_connect(creds_file: str) -> tuple[MagicMock, str]:
            mock_client = MagicMock()
            mock_client.send_message.return_value = {}
            return mock_client, "sender@example.com"

        import smtplib

        def mock_send_error(*args: object, **kwargs: object) -> None:
            raise smtplib.SMTPException("SMTP send failed")

        with (
            patch("app.services.email_automation.connect", mock_connect),
            patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview),
            patch("app.services.email_automation.send_connected_email", mock_send_error),
        ):
            result = _drain(factory, settings, clock, kinds=("onboarding",))

        assert result["uncertain"] == 1, f"Expected 1 uncertain, got {result}"

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "uncertain", f"Expected uncertain, got {final_row.status}"
            assert final_row.submitting_at is not None, "submitting_at should be set"


class TestDrainConnectError:
    """Test that connect errors result in queued (retryable)."""

    def test_connect_error_requeues(self, session: Session, clock: Clock) -> None:
        """A connect error should requeue the row, not mark uncertain.

        MUST fail on PR #65 head if connect errors go uncertain.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None, "Need seeded property"

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Connect Error",
            phone_ciphertext=protect_text("+15551234568", settings.field_encryption_secret),
            email_ciphertext=protect_text("connect@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-connect:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-connect-error"
        snapshot = {
            "recipient": "connect@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-connect-error-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=0,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview(*args: object, **kwargs: object) -> dict[str, object]:
            return {
                "recipient": "connect@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
                "fingerprint": test_fingerprint,
            }

        def mock_connect_fail(creds_file: str) -> tuple[MagicMock, str]:
            raise OSError("Connection refused")

        with (
            patch("app.services.email_automation.connect", mock_connect_fail),
            patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview),
        ):
            result = _drain(factory, settings, clock, kinds=("onboarding",))

        assert result["blocked"] == 1, f"Expected 1 blocked (requeued), got {result}"

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "queued", f"Expected queued, got {final_row.status}"
            assert final_row.submitting_at is None, "submitting_at should NOT be set for connect error"


class TestDrainKeyError:
    """Test that KeyError during validation results in failed."""

    def test_keyerror_marks_failed(self, session: Session, clock: Clock) -> None:
        """A KeyError during validation should mark the row failed.

        MUST fail on PR #65 head where KeyError was requeued.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None, "Need seeded property"

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner KeyError",
            phone_ciphertext=protect_text("+15551234569", settings.field_encryption_secret),
            email_ciphertext=protect_text("keyerror@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-keyerror:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-keyerror"
        snapshot = {
            "recipient": "keyerror@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-keyerror-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=0,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview_keyerror(*args: object, **kwargs: object) -> dict[str, object]:
            raise KeyError("missing_field")

        with patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview_keyerror):
            result = _drain(factory, settings, clock, kinds=("onboarding",))

        assert result["blocked"] == 1, f"Expected 1 blocked (failed), got {result}"

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "failed", f"Expected failed, got {final_row.status}"
            assert final_row.error_code == "invalid_snapshot"

            exception = s.scalar(
                select(OperationalException).where(OperationalException.dedupe_key == f"email-delivery:{delivery_id}")
            )
            assert exception is not None, "Should create operational exception"


class TestDrainStrandedRecovery:
    """Test stranded row recovery based on submitting_at marker."""

    def test_stranded_without_marker_requeues(self, session: Session, clock: Clock) -> None:
        """A stranded row WITHOUT submitting_at should be requeued.

        MUST fail on PR #65 head if it goes uncertain.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Stranded",
            phone_ciphertext=protect_text("+15551234570", settings.field_encryption_secret),
            email_ciphertext=protect_text("stranded@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-stranded:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        snapshot = {"recipient": "stranded@example.com", "subject": "Test", "body": "Test"}
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now() - timedelta(minutes=10),
            updated_at=clock.now() - timedelta(minutes=6),
            version=2,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-stranded-{delivery_id}",
            fingerprint="test-fingerprint-stranded",
            status="sending",
            claimed_at=clock.now() - timedelta(minutes=6),
            submitting_at=None,
            next_attempt_at=clock.now() - timedelta(minutes=6),
            attempt_count=1,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        _drain(factory, settings, clock, kinds=("onboarding",))

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "queued", f"Expected queued, got {final_row.status}"
            assert final_row.error_code == "interrupted_before_send"

    def test_stranded_with_marker_goes_uncertain(self, session: Session, clock: Clock) -> None:
        """A stranded row WITH submitting_at should be marked uncertain.

        This is because we don't know if the email was actually sent.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Stranded Marker",
            phone_ciphertext=protect_text("+15551234571", settings.field_encryption_secret),
            email_ciphertext=protect_text("stranded-marker@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-stranded-marker:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        snapshot = {"recipient": "stranded-marker@example.com", "subject": "Test", "body": "Test"}
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now() - timedelta(minutes=10),
            updated_at=clock.now() - timedelta(minutes=6),
            version=2,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-stranded-marker-{delivery_id}",
            fingerprint="test-fingerprint-stranded-marker",
            status="sending",
            claimed_at=clock.now() - timedelta(minutes=6),
            submitting_at=clock.now() - timedelta(minutes=6),
            next_attempt_at=clock.now() - timedelta(minutes=6),
            attempt_count=1,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        result = _drain(factory, settings, clock, kinds=("onboarding",))

        assert result["uncertain"] == 1, f"Expected 1 uncertain, got {result}"

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "uncertain", f"Expected uncertain, got {final_row.status}"


class TestDrainRowReclaimed:
    """Test that re-claimed rows are not sent."""

    def test_reclaimed_row_not_sent(self, session: Session, clock: Clock) -> None:
        """If a row is re-claimed (status changed) before send, don't send.

        MUST fail on PR #65 head if it sends anyway.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Reclaimed",
            phone_ciphertext=protect_text("+15551234572", settings.field_encryption_secret),
            email_ciphertext=protect_text("reclaimed@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-reclaimed:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-reclaimed"
        snapshot = {
            "recipient": "reclaimed@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-reclaimed-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=0,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        send_called = []

        def mock_onboarding_preview(*args: object, **kwargs: object) -> dict[str, object]:
            return {
                "recipient": "reclaimed@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
                "fingerprint": test_fingerprint,
            }

        def mock_connect(creds_file: str) -> tuple[MagicMock, str]:
            with factory() as s:
                row = s.get(EmailDelivery, delivery_id)
                if row is not None:
                    row.status = "stale"
                    row.version += 10
                    s.commit()
            mock_client = MagicMock()

            def track_send(*args: object, **kwargs: object) -> None:
                send_called.append(True)

            mock_client.send_message = track_send
            return mock_client, "sender@example.com"

        with (
            patch("app.services.email_automation.connect", mock_connect),
            patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview),
        ):
            _drain(factory, settings, clock, kinds=("onboarding",))

        assert len(send_called) == 0, "Should not have called send after row was reclaimed"


class TestDrainRetryCap:
    """Test retry cap behavior in _drain."""

    def test_stranded_at_attempt_3_without_marker_ends_failed(self, session: Session, clock: Clock) -> None:
        """A stranded sending row at attempt_count=3 with no marker ends failed.

        When a row is stuck in 'sending' status at max retries and has no submitting_at
        marker, it should be marked 'failed' with EMAIL_DELIVERY_FAILED problem.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Cap",
            phone_ciphertext=protect_text("+15551234599", settings.field_encryption_secret),
            email_ciphertext=protect_text("cap@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-cap:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-cap"
        snapshot = {
            "recipient": "cap@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-cap-{delivery_id}",
            fingerprint=test_fingerprint,
            status="sending",
            claimed_at=clock.now() - timedelta(minutes=10),
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=3,
            submitting_at=None,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview(*args: object, **kwargs: object) -> dict[str, object]:
            return {
                "recipient": "cap@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
                "fingerprint": test_fingerprint,
            }

        with patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview):
            _drain(factory, settings, clock, kinds=("onboarding",))

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "failed", f"Expected failed, got {final_row.status}"
            assert final_row.error_code == "interrupted_before_send"

            problem = s.scalar(
                select(OperationalException).where(
                    OperationalException.code == "EMAIL_DELIVERY_FAILED",
                    OperationalException.status != "resolved",
                )
            )
            assert problem is not None, "Should have created EMAIL_DELIVERY_FAILED problem"

    def test_validation_error_at_attempt_3_ends_failed_with_problem(self, session: Session, clock: Clock) -> None:
        """A RuntimeError in validation on the 3rd attempt ends failed with problem.

        When prepare() raises an exception on the 3rd attempt, the row should be
        marked 'failed' with error code 'pre_send_error'.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Val3",
            phone_ciphertext=protect_text("+15551234598", settings.field_encryption_secret),
            email_ciphertext=protect_text("val3@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-val3:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-val3"
        snapshot = {
            "recipient": "val3@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-val3-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=2,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview_error(*args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("Validation failed on 3rd attempt")

        with patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview_error):
            _drain(factory, settings, clock, kinds=("onboarding",))

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "failed", f"Expected failed, got {final_row.status}"
            assert final_row.error_code == "pre_send_error"

            problem = s.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"email-delivery:{delivery_id}",
                    OperationalException.status != "resolved",
                )
            )
            assert problem is not None, f"Should have created problem with key email-delivery:{delivery_id}"

    def test_validation_error_at_attempt_1_ends_queued(self, session: Session, clock: Clock) -> None:
        """A RuntimeError in validation on the 1st attempt ends queued with pre_send_error.

        When prepare() raises an exception on the 1st attempt, the row should be
        requeued for retry with error code 'pre_send_error'.
        """
        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)

        prop = session.scalars(select(Property).limit(1)).first()
        assert prop is not None

        cleaner_id = uuid.uuid4()
        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner Val1",
            phone_ciphertext=protect_text("+15551234597", settings.field_encryption_secret),
            email_ciphertext=protect_text("val1@example.com", settings.field_encryption_secret),
            phone_lookup_hmac=hashlib.sha256(f"pending-phone-val1:{cleaner_id}".encode()).hexdigest(),
            active=True,
            sms_consent_status="pending",
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        session.add(cleaner)
        session.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[],
        )
        session.add(weekly)
        session.flush()

        grant_id = uuid.uuid4()
        token = onboarding_token(grant_id, settings, workspace_id)
        grant = CleanerAccessLink(
            id=grant_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=7),
            purpose="onboarding",
        )
        session.add(grant)
        session.flush()

        test_fingerprint = "test-fingerprint-val1"
        snapshot = {
            "recipient": "val1@example.com",
            "subject": "Complete your onboarding",
            "body": "Please complete your onboarding.",
        }
        delivery_id = uuid.uuid4()
        delivery = EmailDelivery(
            id=delivery_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            cleaner_id=cleaner_id,
            onboarding_grant_id=grant_id,
            kind="onboarding",
            service_date=clock.now().date(),
            message_key=f"test-val1-{delivery_id}",
            fingerprint=test_fingerprint,
            status="queued",
            next_attempt_at=clock.now() - timedelta(minutes=1),
            attempt_count=0,
            snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
        )
        session.add(delivery)
        session.commit()

        def mock_onboarding_preview_error(*args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("Validation failed on 1st attempt")

        with patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview_error):
            _drain(factory, settings, clock, kinds=("onboarding",))

        with factory() as s:
            final_row = s.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "queued", f"Expected queued, got {final_row.status}"
            assert final_row.error_code == "pre_send_error"
            assert final_row.attempt_count == 1
