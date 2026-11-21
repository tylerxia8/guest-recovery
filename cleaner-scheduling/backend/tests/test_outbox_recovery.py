"""Tests for outbox email recovery and H2/H3/M3 fixes."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy
from app.clock import Clock
from app.config import Settings
from app.models import OperationalException, OutboxEvent
from app.services.field_cipher import protect_text
from app.services.outbox_email import (
    STUCK_SENDING_THRESHOLD_MINUTES,
    deliver_outbox_email,
    recover_stuck_outbox_emails,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def test_settings(settings: Settings) -> Settings:
    return settings


class TestRecoverStuckOutboxEmails:
    """Tests for recover_stuck_outbox_emails (H2/H3 fix).

    Lease-Expired Behavior (outbox_email.py:294-366):
    When a row is stuck in 'sending' status and its lease (claimed_until) has expired:
    - If submitting_at marker exists: mark 'uncertain' (SMTP submission may have started)
    - If no submitting_at marker: re-queue if attempts < 3, else mark 'failed'

    This is distinct from claim_outbox_email:50-52, which handles the claim-time
    check: if status is already 'sending' when a new claim starts, it means the
    previous worker crashed, so mark uncertain immediately.
    """

    def test_recovers_stuck_sending_rows(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """Rows stuck in 'sending' past lease with submitting_at should be marked 'uncertain'.

        This tests the lease-expired path at recover_stuck_outbox_emails:319-335.
        The submitting_at marker indicates SMTP submission may have started.

        Mutation: Remove submitting_at check at outbox_email.py:318.
        Fails: Row would be requeued instead of marked uncertain.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
                "submitting_at": (now - timedelta(minutes=6)).isoformat(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        recovered = recover_stuck_outbox_emails(session, clock, "test.email")

        assert recovered == 1
        session.refresh(event)
        assert event.payload["status"] == "uncertain"
        assert event.last_error_code == "interrupted_send"
        assert event.processed_at is not None

        exception = session.scalar(
            select(OperationalException).where(OperationalException.dedupe_key == f"outbox-email:{event_id}")
        )
        assert exception is not None
        assert exception.code == "OUTBOX_EMAIL_UNCERTAIN"
        assert exception.status == "open"

    def test_ignores_recent_sending_rows(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """Rows in 'sending' but not past the threshold should not be recovered."""
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=2),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=2),
            claimed_until=now + timedelta(minutes=3),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        recovered = recover_stuck_outbox_emails(session, clock, "test.email")

        assert recovered == 0
        session.refresh(event)
        assert event.payload["status"] == "sending"
        assert event.processed_at is None

    def test_ignores_other_topics(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """Recovery should only affect the specified topic."""
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="other.topic",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        recovered = recover_stuck_outbox_emails(session, clock, "test.email")

        assert recovered == 0
        session.refresh(event)
        assert event.payload["status"] == "sending"


class TestDeliverOutboxEmailErrorHandling:
    """Tests for deliver_outbox_email error handling (H3 fix)."""

    def test_send_error_marks_uncertain(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """Errors during SMTP submission should mark uncertain.

        After the submitting_at marker is set, we don't know if the email was sent,
        so it must be marked uncertain.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def prepare() -> dict[str, str]:
            return snapshot

        def failing_send(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Send failure")

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            with patch("app.services.outbox_email.send_connected_email", failing_send):
                deliver_outbox_email(session, event, test_settings, clock, prepare)

        session.refresh(event)
        assert event.payload["status"] == "uncertain"
        assert event.last_error_code == "send_outcome_unknown"
        assert event.processed_at is not None

    def test_smtp_5xx_rejection_fails_permanently(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """SMTP 5xx errors (permanent rejection) should mark failed, not uncertain.

        5xx errors indicate permanent rejection by the server (bad recipient,
        message rejected, etc.). These should not be retried.

        Mutation: Remove SMTPRecipientsRefused/SMTPSenderRefused/SMTPDataError check
                  at outbox_email.py:190.
        Fails: Status would be 'uncertain' instead of 'failed'.
        """
        import smtplib

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-5xx:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def prepare() -> dict[str, str]:
            return snapshot

        def failing_send(*_args: object, **_kwargs: object) -> None:
            raise smtplib.SMTPRecipientsRefused({"test@example.com": (550, b"User unknown")})

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            with patch("app.services.outbox_email.send_connected_email", failing_send):
                deliver_outbox_email(session, event, test_settings, clock, prepare)

        session.refresh(event)
        assert event.payload["status"] == "failed", (
            f"5xx rejection should fail permanently, got {event.payload['status']}"
        )
        assert event.last_error_code == "message_rejected"

    def test_smtp_data_error_fails_permanently(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """SMTP DATA errors (552 message too large, etc.) should mark failed.

        Mutation: Remove SMTPDataError from the check at outbox_email.py:190.
        Fails: Status would be 'uncertain' instead of 'failed'.
        """
        import smtplib

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-data-err:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def prepare() -> dict[str, str]:
            return snapshot

        def failing_send(*_args: object, **_kwargs: object) -> None:
            raise smtplib.SMTPDataError(552, b"Message too large")

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            with patch("app.services.outbox_email.send_connected_email", failing_send):
                deliver_outbox_email(session, event, test_settings, clock, prepare)

        session.refresh(event)
        assert event.payload["status"] == "failed"
        assert event.last_error_code == "message_rejected"

    def test_smtp_sender_refused_fails_permanently(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """SMTPSenderRefused during send marks row failed immediately.

        outbox_email.py:190-191 handles SMTPSenderRefused as a permanent failure.

        Mutation: Remove SMTPSenderRefused from the isinstance check at :190.
        Fails: assert event.payload["status"] == "failed"
        """
        import smtplib

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-sender-ref:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def prepare() -> dict[str, str]:
            return snapshot

        def failing_send(*_args: object, **_kwargs: object) -> None:
            raise smtplib.SMTPSenderRefused(550, b"Sender rejected", "sender@example.com")

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            with patch("app.services.outbox_email.send_connected_email", failing_send):
                deliver_outbox_email(session, event, test_settings, clock, prepare)

        session.refresh(event)
        assert event.payload["status"] == "failed", (
            f"SMTPSenderRefused should fail permanently, got {event.payload['status']}"
        )
        assert event.last_error_code == "message_rejected"

    def test_prepare_error_requeues(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """Errors during prepare() should requeue, not mark uncertain.

        If prepare() fails, we know no email was sent, so we can safely requeue.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def failing_prepare() -> dict[str, str]:
            raise RuntimeError("DB connection lost")

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            deliver_outbox_email(session, event, test_settings, clock, failing_prepare)

        session.refresh(event)
        assert event.payload["status"] == "queued"
        assert event.last_error_code == "pre_send_error"
        assert event.processed_at is None
        assert "submitting_at" not in event.payload
        assert event.attempt_count == 1, f"attempt_count should be incremented to 1, got {event.attempt_count}"

    def test_prepare_operational_error_requeues(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """OperationalError during second prepare() should requeue with pre_send_error.

        MUST fail on pre-fix code that doesn't handle OperationalError properly.
        """
        from sqlalchemy.exc import OperationalError

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def failing_prepare() -> dict[str, str]:
            raise OperationalError("connection lost", params=None, orig=Exception("test"))

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        with patch("app.services.outbox_email.connect") as mock_connect:
            mock_connect.return_value = (mock_client, "sender@example.com")
            deliver_outbox_email(session, event, test_settings, clock, failing_prepare)

        session.refresh(event)
        assert event.payload["status"] == "queued", "OperationalError should requeue, not mark uncertain"
        assert event.last_error_code == "pre_send_error"
        assert event.processed_at is None
        assert "submitting_at" not in event.payload
        assert event.attempt_count == 1, f"attempt_count should be incremented to 1, got {event.attempt_count}"

    def test_uncertain_creates_operational_exception(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """When a delivery goes uncertain, an operational exception should be created."""
        now = clock.now()
        event_id = uuid.uuid4()
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=6),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={"status": "sending"},
            available_at=now - timedelta(minutes=6),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        deliver_outbox_email(session, event, test_settings, clock, lambda: {})

        session.refresh(event)
        assert event.payload["status"] == "uncertain"

        exception = session.scalar(
            select(OperationalException).where(OperationalException.dedupe_key == f"outbox-email:{event_id}")
        )
        assert exception is not None
        assert exception.code == "OUTBOX_EMAIL_UNCERTAIN"


class TestGuardSkipBehavior:
    """Tests for guard skip semantics at claim_outbox_email:50-52.

    When claim_outbox_email encounters an event already in 'sending' status (from
    a previous claim that may have crashed), it marks it 'uncertain' via _finish().

    IMPORTANT: A guard skip does NOT count as a new attempt because:
    1. The previous claim already incremented attempt_count
    2. We don't know if that attempt actually sent the email
    3. Incrementing again would double-count the original attempt
    """

    def test_sending_status_marked_uncertain_without_attempt_increment(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A 'sending' event at claim time is marked uncertain, attempt_count unchanged.

        Mutation: Add attempt_count += 1 at outbox_email.py:51 before _finish.
        Fails: assert event.attempt_count == 2
        """
        from app.services.outbox_email import claim_outbox_email

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=5),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-guard-skip:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=5),
            attempt_count=2,
        )
        session.add(event)
        session.commit()

        result = claim_outbox_email(session, event, test_settings, clock, lambda: snapshot)

        assert result is None
        session.refresh(event)
        assert event.payload["status"] == "uncertain"
        assert event.last_error_code == "interrupted_send"
        assert event.attempt_count == 2, (
            f"attempt_count should stay at 2 (guard skip does not increment), got {event.attempt_count}"
        )
        assert event.processed_at is not None


class TestDeliverOutboxEmailGuards:
    """Tests for pre_send_guard (:137-150) and final_guard (:174-186) in deliver_outbox_email.

    These guards check status and version before proceeding. If the event was modified
    by another process, the guard skips the operation. Guard skips do NOT count as
    an attempt because the email may have been sent by the other process.
    """

    def test_pre_send_guard_skips_if_status_changed(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """pre_send_guard (:137-150) skips if status changes after claim but before send.

        Between claim and pre_send check, another process may have changed the status.
        The guard logs and returns without sending. This simulates a race where
        another process marks the event failed/stale after we claim it.

        Mutation: Remove status check at outbox_email.py:139.
        Fails: Would attempt to send even when status changed to 'stale'.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-pre-guard:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        send_called = False

        def mock_connect(_creds: str) -> tuple[MagicMock, str]:
            session.execute(
                sqlalchemy.text("UPDATE outbox_events SET payload = payload || :change WHERE id = :id"),
                {"id": event_id, "change": json.dumps({"status": "stale"})},
            )
            session.commit()
            mock_client = MagicMock()
            mock_client.close = MagicMock()
            return mock_client, "sender@example.com"

        def mock_send(*_args: object, **_kwargs: object) -> None:
            nonlocal send_called
            send_called = True

        with patch("app.services.outbox_email.connect", mock_connect):
            with patch("app.services.outbox_email.send_connected_email", mock_send):
                deliver_outbox_email(session, event, test_settings, clock, lambda: snapshot)

        assert not send_called, "pre_send_guard should skip when status changed to 'stale'"

        session.expire_all()
        final = session.get(OutboxEvent, event_id)
        assert final is not None
        assert final.payload.get("status") == "stale", "status should be stale (set by mock)"
        assert final.version == 2, "version incremented by claim, stays at 2"
        assert final.payload.get("submitting_at") is None, "no submitting_at after guard skip"
        assert final.attempt_count == 1, "attempt_count incremented by claim"

    def test_pre_send_guard_skips_if_version_mismatch(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """pre_send_guard (:137-150) skips if version doesn't match.

        Another process may have incremented version. Guard logs and returns.

        Mutation: Remove version check at outbox_email.py:140.
        Fails: Would attempt to send with stale version.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-ver-guard:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        send_called = False

        def mock_connect(_creds: str) -> tuple[MagicMock, str]:
            session.execute(
                sqlalchemy.text("UPDATE outbox_events SET version = version + 1 WHERE id = :id"),
                {"id": event_id},
            )
            session.commit()
            mock_client = MagicMock()
            mock_client.close = MagicMock()
            return mock_client, "sender@example.com"

        def mock_send(*_args: object, **_kwargs: object) -> None:
            nonlocal send_called
            send_called = True

        with patch("app.services.outbox_email.connect", mock_connect):
            with patch("app.services.outbox_email.send_connected_email", mock_send):
                deliver_outbox_email(session, event, test_settings, clock, lambda: snapshot)

        assert not send_called, "pre_send_guard should skip on version mismatch"

        session.expire_all()
        final = session.get(OutboxEvent, event_id)
        assert final is not None
        assert final.payload.get("status") == "sending", "status is sending (set by claim)"
        assert final.version == 3, "version 1→2 (claim) then 2→3 (mock increment)"
        assert final.payload.get("submitting_at") is None, "no submitting_at after guard skip"
        assert final.attempt_count == 1, "attempt_count incremented by claim"


class TestNormalErrorAtAttemptCap:
    """Tests for normal (non-permanent) errors when attempt_count reaches cap."""

    def test_normal_error_at_cap_fails_permanently(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A normal error (RuntimeError) at attempt cap should mark failed, not requeue.

        When attempt_count reaches 3 and a non-permanent error occurs during connect,
        it should be marked 'failed' with 'connection_failed', not requeued.

        Mutation: Change >= 3 to > 3 in outbox_email.py:196.
        Fails: assert event.payload["status"] == "failed"
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-cap:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=2,
        )
        session.add(event)
        session.commit()

        def mock_prepare() -> dict[str, str]:
            return snapshot

        def mock_connect(_creds_file: str) -> tuple[MagicMock, str]:
            raise OSError("Transient connection error")

        with patch("app.services.outbox_email.connect", mock_connect):
            deliver_outbox_email(session, event, test_settings, clock, mock_prepare)

        session.refresh(event)
        assert event.payload["status"] == "failed", (
            f"At attempt cap (3), should fail permanently, got {event.payload['status']}"
        )
        assert event.last_error_code == "connection_failed"
        assert event.processed_at is not None
        assert event.attempt_count == 3


class TestStuckSendingThreshold:
    """Tests for the stuck sending threshold constant."""

    def test_threshold_is_five_minutes(self) -> None:
        """The stuck sending threshold should be 5 minutes."""
        assert STUCK_SENDING_THRESHOLD_MINUTES == 5


class TestClaimOutboxEmailValidationErrors:
    """Tests for claim_outbox_email validation error handling."""

    def test_validation_keyerror_marks_failed(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """KeyError during validation (snapshot parsing) should mark failed with invalid_snapshot.

        When the snapshot is malformed and causes a KeyError during prepare(),
        it should be marked 'failed' with error code 'invalid_snapshot'.
        """
        from app.services.outbox_email import claim_outbox_email

        now = clock.now()
        event_id = uuid.uuid4()
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps({}), test_settings.field_encryption_secret).hex(),
            },
            available_at=now,
            attempt_count=0,
        )
        session.add(event)
        session.commit()

        def prepare_with_keyerror() -> dict[str, str]:
            raise KeyError("missing_field")

        result = claim_outbox_email(session, event, test_settings, clock, prepare_with_keyerror)

        assert result is None
        session.refresh(event)
        assert event.payload["status"] == "failed"
        assert event.last_error_code == "invalid_snapshot"
        assert event.processed_at is not None


class TestClaimOutboxEmailExceptionReclaim:
    """Tests for claim_outbox_email except-Exception path with concurrent re-claim.

    CRITICAL: session.rollback() at :65 drops the row and workspace locks. The code
    then re-locks and reloads, but without checking status or version another worker
    can claim the row between rollback and reload. The fix captures claimed_version
    before prepare() and checks it after reload.

    All tests use a SEPARATE session for the re-claimer to simulate real concurrency.
    """

    def test_reclaimed_row_during_prepare_exception_not_modified_low_attempts(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """When prepare() raises Exception and row is re-claimed, stale worker must not modify it.

        Case: attempt_count < 3 (would normally requeue with pre_send_error).

        MUST fail on cbe2bf3 (without claim guard) because the stale worker's
        reload writes without checking version/status.
        """
        from app.db import session_factory
        from app.services.outbox_email import claim_outbox_email
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-claim-reclaim-low:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        new_claimer_version: int | None = None

        def prepare_reclaims_then_raises() -> dict[str, str]:
            nonlocal new_claimer_version
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending"}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                new_claimer_version = other_event.version
                other_session.commit()
            raise RuntimeError("Simulated prepare failure after re-claim")

        result = claim_outbox_email(session, event, test_settings, clock, prepare_reclaims_then_raises)

        assert result is None

        with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as check_session:
            final = check_session.get(OutboxEvent, event_id)
            assert final is not None
            assert final.payload["status"] == "sending", (
                f"Row should stay 'sending' (new claimer's state), got {final.payload['status']}"
            )
            assert final.version == new_claimer_version, (
                f"Version should be {new_claimer_version} (new claimer's), got {final.version}"
            )
            assert final.attempt_count == 1, f"attempt_count should stay 1 (original), got {final.attempt_count}"
            assert final.processed_at is None, "Row should NOT be processed"

            problem = check_session.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"outbox-email:{event_id}",
                    OperationalException.code == "EMAIL_DELIVERY_FAILED",
                )
            )
            assert problem is None, "Should NOT have created EMAIL_DELIVERY_FAILED problem"

    def test_reclaimed_row_during_prepare_exception_not_modified_at_cap(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """When prepare() raises Exception and row is re-claimed, stale worker must not modify it.

        Case: attempt_count = 2 (would hit cap at 3 and call _finish('failed')).

        MUST fail on cbe2bf3 (without claim guard) because the stale worker's
        reload writes without checking version/status, marking the row failed
        and opening EMAIL_DELIVERY_FAILED after the email may already have gone out.
        """
        from app.db import session_factory
        from app.services.outbox_email import claim_outbox_email
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-claim-reclaim-cap:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=2,
        )
        session.add(event)
        session.commit()

        new_claimer_version: int | None = None

        def prepare_reclaims_then_raises() -> dict[str, str]:
            nonlocal new_claimer_version
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending"}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                new_claimer_version = other_event.version
                other_session.commit()
            raise RuntimeError("Simulated prepare failure after re-claim")

        result = claim_outbox_email(session, event, test_settings, clock, prepare_reclaims_then_raises)

        assert result is None

        with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as check_session:
            final = check_session.get(OutboxEvent, event_id)
            assert final is not None
            assert final.payload["status"] == "sending", (
                f"Row should stay 'sending' (new claimer's state), got {final.payload['status']}"
            )
            assert final.version == new_claimer_version, (
                f"Version should be {new_claimer_version} (new claimer's), got {final.version}"
            )
            assert final.attempt_count == 2, f"attempt_count should stay 2 (original), got {final.attempt_count}"
            assert final.processed_at is None, "Row should NOT be processed (no _finish called)"

            problem = check_session.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"outbox-email:{event_id}",
                    OperationalException.code == "EMAIL_DELIVERY_FAILED",
                )
            )
            assert problem is None, "Should NOT have created EMAIL_DELIVERY_FAILED problem"


class TestRecoverStuckWithoutSubmittingAt:
    """Tests for recover_stuck_outbox_emails with submitting_at marker.

    MUST fail on b24b093 or 5e47fdb where rows without submitting_at would be
    marked 'uncertain' instead of requeued.
    """

    def test_stuck_without_submitting_at_is_requeued(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """Rows stuck in 'sending' WITHOUT submitting_at should be requeued, not uncertain.

        This covers the case where we crashed BEFORE starting to send. We know
        no email was sent, so it's safe to retry.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        recovered = recover_stuck_outbox_emails(session, clock, "test.email")

        assert recovered == 1
        session.refresh(event)
        assert event.payload["status"] == "queued", "Without submitting_at, should be requeued not uncertain"
        assert event.last_error_code == "recovered_pre_send"
        assert event.processed_at is None

    def test_stuck_with_submitting_at_is_uncertain(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """Rows stuck in 'sending' WITH submitting_at should be marked uncertain.

        This covers the case where we crashed DURING/AFTER starting to send.
        We don't know if the email was delivered, so mark uncertain.
        """
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
                "submitting_at": (now - timedelta(minutes=6)).isoformat(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        recovered = recover_stuck_outbox_emails(session, clock, "test.email")

        assert recovered == 1
        session.refresh(event)
        assert event.payload["status"] == "uncertain", "With submitting_at, should be uncertain"
        assert event.last_error_code == "interrupted_send"
        assert event.processed_at is not None


class TestOutboxEmailProblemCreation:
    """Tests for EMAIL_DELIVERY_FAILED problem creation in outbox email."""

    def test_failed_via_finish_creates_problem(self, session: Session, clock: Clock, test_settings: Settings) -> None:
        """A terminal failed state via _finish opens outbox-email:{id} problem."""
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-finish:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=3,
        )
        session.add(event)
        session.commit()

        def mock_prepare() -> dict[str, str]:
            return snapshot

        def mock_connect(creds_file: str) -> tuple[MagicMock, str]:
            raise ValueError("Permanent connection error")

        with patch("app.services.outbox_email.connect", mock_connect):
            deliver_outbox_email(session, event, test_settings, clock, mock_prepare)

        session.refresh(event)
        assert event.payload["status"] == "failed"

        problem = session.scalar(
            select(OperationalException).where(
                OperationalException.dedupe_key == f"outbox-email:{event_id}",
                OperationalException.code == "EMAIL_DELIVERY_FAILED",
            )
        )
        assert problem is not None, f"Should have created problem with key outbox-email:{event_id}"

    def test_failed_via_max_attempts_creates_problem(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A terminal failed state via max_attempts_pre_send opens outbox-email:{id} problem."""
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-max-attempts:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=3,
        )
        session.add(event)
        session.commit()

        recover_stuck_outbox_emails(session, clock, "test.email")

        session.refresh(event)
        assert event.payload["status"] == "failed"
        assert event.last_error_code == "max_attempts_pre_send"

        problem = session.scalar(
            select(OperationalException).where(
                OperationalException.dedupe_key == f"outbox-email:{event_id}",
                OperationalException.code == "EMAIL_DELIVERY_FAILED",
            )
        )
        assert problem is not None, f"Should have created problem with key outbox-email:{event_id}"

    def test_exactly_one_problem_for_failed_event(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """Both _finish and max_attempts_pre_send paths create exactly one problem per event."""
        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=6),
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-one-problem:{event_id}",
            payload={
                "status": "sending",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=10),
            claimed_until=now - timedelta(minutes=6),
            attempt_count=3,
        )
        session.add(event)
        session.commit()

        recover_stuck_outbox_emails(session, clock, "test.email")
        recover_stuck_outbox_emails(session, clock, "test.email")

        problems = list(
            session.scalars(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"outbox-email:{event_id}",
                )
            )
        )
        assert len(problems) == 1, f"Should have exactly one problem, got {len(problems)}"
        assert problems[0].code == "EMAIL_DELIVERY_FAILED"


class TestStaleWorkerVersionCheck:
    """Tests for version/status guards with populate_existing to prevent double-send.

    CRITICAL: session_factory uses expire_on_commit=False, so select().with_for_update()
    returns the SAME in-memory object without refreshing. Guards comparing stale values
    never fire unless populate_existing=True is used.

    All tests use a SEPARATE session/connection for the re-claimer to simulate real
    concurrent workers properly.
    """

    def test_reclaimed_row_not_requeued_by_stale_worker(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A stale worker must not re-queue a row that was re-claimed by another worker.

        When connect fails and the row was re-claimed (version bumped) while waiting,
        the stale worker should leave the row as the new claimer set it.

        MUST fail on 8c69a9f (without populate_existing) because the stale worker's
        final_event re-select returns stale data and sets the row back to 'queued'.
        """
        from app.db import session_factory
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-stale-worker:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        new_claimer_version: int | None = None

        def mock_prepare() -> dict[str, str]:
            return snapshot

        def mock_connect_reclaims(creds_file: str) -> tuple[MagicMock, str]:
            nonlocal new_claimer_version
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending"}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                new_claimer_version = other_event.version
                other_session.commit()
            raise OSError("Connection failed after re-claim")

        with patch("app.services.outbox_email.connect", mock_connect_reclaims):
            deliver_outbox_email(session, event, test_settings, clock, mock_prepare)

        with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as check_session:
            final = check_session.get(OutboxEvent, event_id)
            assert final is not None
            assert final.payload["status"] == "sending", (
                f"Row should stay 'sending' (new claimer's state), got {final.payload['status']}"
            )
            assert final.version == new_claimer_version, (
                f"Version should be {new_claimer_version} (new claimer's), got {final.version}"
            )

    def test_stale_worker_does_not_send_after_reclaim(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A stale worker must not send after a re-claim (pre-send check).

        When connect succeeds but the row was re-claimed (version bumped) during connect,
        send_connected_email should NEVER be called.

        MUST fail on 8c69a9f (without populate_existing) because pre_send_event re-select
        returns stale data, guard passes, and send is called.
        """
        from app.db import session_factory
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-stale-presend:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        def mock_prepare() -> dict[str, str]:
            return snapshot

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        def mock_connect_reclaims(creds_file: str) -> tuple[MagicMock, str]:
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending"}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                other_session.commit()
            return (mock_client, "sender@example.com")

        with patch("app.services.outbox_email.connect", mock_connect_reclaims):
            with patch("app.services.outbox_email.send_connected_email") as mock_send:
                deliver_outbox_email(session, event, test_settings, clock, mock_prepare)
                mock_send.assert_not_called()

    def test_stale_worker_at_max_attempts_does_not_fail_reclaimed_row(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A stale worker at attempt_count=3 must not mark a re-claimed row failed.

        When a stale worker's connect fails at max attempts and the row was re-claimed,
        it should NOT call _finish('failed') and should NOT create EMAIL_DELIVERY_FAILED.

        MUST fail on 8c69a9f (without populate_existing) because final_event re-select
        returns stale data, guard passes, and _finish('failed') overwrites the row.
        """
        from app.db import session_factory
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-stale-maxattempts:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=3,
        )
        session.add(event)
        session.commit()

        new_claimer_version: int | None = None

        def mock_prepare() -> dict[str, str]:
            return snapshot

        def mock_connect_reclaims(creds_file: str) -> tuple[MagicMock, str]:
            nonlocal new_claimer_version
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending"}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                new_claimer_version = other_event.version
                other_session.commit()
            raise OSError("Connection failed after re-claim")

        with patch("app.services.outbox_email.connect", mock_connect_reclaims):
            deliver_outbox_email(session, event, test_settings, clock, mock_prepare)

        with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as check_session:
            final = check_session.get(OutboxEvent, event_id)
            assert final is not None
            assert final.payload["status"] == "sending", (
                f"Row should stay 'sending' (new claimer's state), got {final.payload['status']}"
            )
            assert final.version == new_claimer_version, (
                f"Version should be {new_claimer_version} (new claimer's), got {final.version}"
            )
            assert final.processed_at is None, "Row should NOT be processed (no _finish called)"

            problem = check_session.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == f"outbox-email:{event_id}",
                    OperationalException.code == "EMAIL_DELIVERY_FAILED",
                )
            )
            assert problem is None, "Should NOT have created EMAIL_DELIVERY_FAILED problem"

    def test_stale_worker_in_submitted_branch_does_not_overwrite_reclaimed_row(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """A stale worker in the submitted branch must not overwrite a re-claimed row.

        When send succeeds but the row was re-claimed during/after send, the stale
        worker should NOT call _finish and should NOT overwrite the new claimer's state.

        MUST fail on 8c69a9f (without populate_existing) because final_event re-select
        returns stale data, guard passes, and _finish('accepted') overwrites the row.
        """
        from app.db import session_factory
        from app.workspaces import LEGACY_WORKSPACE_ID

        now = clock.now()
        event_id = uuid.uuid4()
        snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
        event = OutboxEvent(
            id=event_id,
            created_at=now,
            updated_at=now,
            version=1,
            topic="test.email",
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            intent_key=f"test-stale-submitted:{event_id}",
            payload={
                "status": "queued",
                "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
            },
            available_at=now - timedelta(minutes=1),
            claimed_until=None,
            attempt_count=1,
        )
        session.add(event)
        session.commit()

        new_claimer_version: int | None = None

        def mock_prepare() -> dict[str, str]:
            return snapshot

        mock_client = MagicMock()
        mock_client.close = MagicMock()

        def mock_connect_ok(creds_file: str) -> tuple[MagicMock, str]:
            return (mock_client, "sender@example.com")

        def mock_send_reclaims(*args: object, **kwargs: object) -> None:
            nonlocal new_claimer_version
            with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as other_session:
                other_event = other_session.get(OutboxEvent, event_id)
                assert other_event is not None
                other_event.payload = {**other_event.payload, "status": "sending", "submitting_at": now.isoformat()}
                other_event.claimed_until = clock.now() + timedelta(minutes=5)
                other_event.version += 1
                new_claimer_version = other_event.version
                other_session.commit()

        with patch("app.services.outbox_email.connect", mock_connect_ok):
            with patch("app.services.outbox_email.send_connected_email", mock_send_reclaims):
                deliver_outbox_email(session, event, test_settings, clock, mock_prepare)

        with session_factory(test_settings, LEGACY_WORKSPACE_ID)() as check_session:
            final = check_session.get(OutboxEvent, event_id)
            assert final is not None
            assert final.payload["status"] == "sending", (
                f"Row should stay 'sending' (new claimer's state), got {final.payload['status']}"
            )
            assert final.version == new_claimer_version, (
                f"Version should be {new_claimer_version} (new claimer's), got {final.version}"
            )
            assert final.processed_at is None, "Row should NOT be processed (no _finish called)"


class TestCrossWorkspaceOutboxRecovery:
    """Security tests: RLS isolation prevents cross-workspace outbox recovery."""

    def test_rls_prevents_cross_workspace_outbox_recovery(
        self, session: Session, clock: Clock, test_settings: Settings
    ) -> None:
        """Outbox recovery in workspace B cannot see or recover events from workspace A.

        Seeds a stuck 'sending' event ONLY in workspace A. Runs recovery from
        workspace B session (RLS-enforced). The event should NOT be recovered.

        Mutation: Drop RLS policy or workspace filter from OutboxEvent.
        Fails: assert recovered == 0
        """
        import os

        import sqlalchemy
        from app.db import get_engine, session_factory
        from sqlalchemy.orm import sessionmaker

        now = clock.now()
        workspace_a = uuid.uuid4()
        workspace_b = uuid.uuid4()
        event_id = uuid.uuid4()

        migrator_factory = sessionmaker(bind=get_engine(os.environ["MIGRATION_DATABASE_URL"]), expire_on_commit=False)
        with migrator_factory() as seed_session:
            for ws_id in (workspace_a, workspace_b):
                seed_session.execute(
                    sqlalchemy.text("INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, :now)"),
                    {"id": ws_id, "name": f"Cross Workspace Outbox Test {ws_id}", "now": now},
                )

            snapshot = {"recipient": "test@example.com", "subject": "Test", "body": "Test body"}
            seed_session.execute(
                sqlalchemy.text(
                    "INSERT INTO outbox_events "
                    "(id, created_at, updated_at, version, topic, aggregate_id, aggregate_version, "
                    "intent_key, payload, available_at, claimed_until, attempt_count, workspace_id) "
                    "VALUES (:id, :created, :updated, :version, :topic, :agg_id, :agg_version, "
                    ":intent_key, :payload, :available, :claimed, :attempts, :ws)"
                ),
                {
                    "id": event_id,
                    "created": now - timedelta(minutes=10),
                    "updated": now - timedelta(minutes=6),
                    "version": 1,
                    "topic": "test.email",
                    "agg_id": uuid.uuid4(),
                    "agg_version": 1,
                    "intent_key": f"cross-ws:{event_id}",
                    "payload": json.dumps(
                        {
                            "status": "sending",
                            "snapshot": protect_text(json.dumps(snapshot), test_settings.field_encryption_secret).hex(),
                            "submitting_at": (now - timedelta(minutes=6)).isoformat(),
                        }
                    ),
                    "available": now - timedelta(minutes=10),
                    "claimed": now - timedelta(minutes=6),
                    "attempts": 1,
                    "ws": workspace_a,
                },
            )
            seed_session.commit()

        factory_b = session_factory(test_settings, workspace_b)
        with factory_b() as b_session:
            recovered_b = recover_stuck_outbox_emails(b_session, clock, "test.email")
            assert recovered_b == 0, (
                f"RLS should prevent workspace B from seeing workspace A's event, but recovered {recovered_b}"
            )

        with migrator_factory() as check_session:
            still_sending = check_session.scalar(sqlalchemy.select(OutboxEvent).where(OutboxEvent.id == event_id))
            assert still_sending is not None
            assert still_sending.payload["status"] == "sending", (
                f"Event should still be 'sending' after B's failed recovery, got {still_sending.payload['status']}"
            )

        factory_a = session_factory(test_settings, workspace_a)
        with factory_a() as a_session:
            recovered_a = recover_stuck_outbox_emails(a_session, clock, "test.email")
            assert recovered_a == 1, (
                f"Positive check: workspace A should recover its own event, but recovered {recovered_a}"
            )

        with migrator_factory() as check_session:
            final = check_session.scalar(sqlalchemy.select(OutboxEvent).where(OutboxEvent.id == event_id))
            assert final is not None
            assert final.payload["status"] == "uncertain", (
                f"Event should be 'uncertain' (had submitting_at) after recovery by A, got {final.payload['status']}"
            )
