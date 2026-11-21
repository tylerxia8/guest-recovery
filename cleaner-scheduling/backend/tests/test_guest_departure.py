"""M4 Guest departure sync tests.

Tests for backend/app/services/guest_departure_sync.py and checkout_policy.py covering:
- Message classification via departure_message
- Error isolation across reservations via run_guest_departure_sync
- Error code recording on checkpoint
- Retry query includes retryable error codes (message_text_unavailable)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.adapters.reservations.hospitable_read import SourceMessage
from app.clock import FrozenClock
from app.config import Settings, clear_settings_cache, get_settings
from app.db import catalog_session_factory, session_factory
from app.models import CleaningTask, OperationalException, OutboxEvent, Property, Reservation
from app.models.tables import ReservationSyncRun
from app.models.workspace_connection import WorkspaceConnection
from app.services.field_cipher import protect_text
from app.services.guest_departure_sync import run_guest_departure_sync
from cryptography.exceptions import InvalidTag
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    pass


@pytest.fixture
def hosted_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings for a hosted workspace with guest departure features enabled."""
    clear_settings_cache()
    settings = get_settings()
    monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))
    monkeypatch.setattr(Settings, "field_encryption_secret", property(lambda self: "fake-secret-for-testing-only"))
    yield settings.model_copy(
        update={
            "hospitable_guest_departure_enabled": True,
            "hospitable_read_only": True,
            "local_scheduling_enabled": True,
            "reservation_provider": "hospitable",
        }
    )
    clear_settings_cache()


def _create_workspace(catalog: Session, clock: FrozenClock, workspace_id: uuid.UUID) -> None:
    """Create a workspace in the catalog (must be called before session_factory)."""
    catalog.execute(
        text("INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, :now)"),
        {"id": workspace_id, "name": "Test Workspace", "now": clock.now()},
    )
    catalog.commit()


def _create_completed_connection(
    session: Session, clock: FrozenClock, workspace_id: uuid.UUID, settings: Settings
) -> bytes:
    """Create a WorkspaceConnection with completed_at set. Returns the token ciphertext."""
    token_ciphertext = protect_text("test-token", settings.field_encryption_secret)
    connection = WorkspaceConnection(
        workspace_id=workspace_id,
        token_ciphertext=token_ciphertext,
        connected_at=clock.now(),
        completed_at=clock.now(),
    )
    session.add(connection)
    session.flush()
    return token_ciphertext


def _create_successful_sync_run(session: Session, clock: FrozenClock) -> None:
    """Create a successful ReservationSyncRun (required to pass publication_blocked check)."""
    now = clock.now()
    run = ReservationSyncRun(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider="hospitable",
        mode="api",
        strategy="incremental",
        status="succeeded",
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
        source_as_of=now - timedelta(minutes=5),
        rows_seen=0,
        rows_applied=0,
        idempotency_key=f"test-sync-{uuid.uuid4()}",
    )
    session.add(run)
    session.flush()


def _create_reservation_with_task(
    session: Session, clock: FrozenClock, settings: Settings, external_id: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a property, reservation, and cleaning task. Returns (property_id, reservation_id, task_id)."""
    now = clock.now()
    property_id = uuid.uuid4()
    reservation_id = uuid.uuid4()
    task_id = uuid.uuid4()

    prop = Property(
        id=property_id,
        created_at=now,
        updated_at=now,
        version=1,
        provider="hospitable",
        external_id=f"prop-{property_id}",
        name="Test Property",
        timezone="America/New_York",
        bedrooms=2,
        location_ciphertext=protect_text("123 Test St", settings.field_encryption_secret),
        geocode_status="synthetic",
        active=True,
    )
    session.add(prop)
    session.flush()

    reservation = Reservation(
        id=reservation_id,
        created_at=now,
        updated_at=now,
        version=1,
        property_id=property_id,
        provider="hospitable",
        external_id=external_id,
        status="active",
        checkin_at=now - timedelta(days=2),
        checkout_at=now + timedelta(hours=6),
        observed_at=now - timedelta(days=2),
        timestamp_provenance="provider",
        payload_digest="test-digest",
    )
    session.add(reservation)
    session.flush()

    task = CleaningTask(
        id=task_id,
        created_at=now,
        updated_at=now,
        version=1,
        property_id=property_id,
        source_reservation_id=reservation_id,
        kind="departure",
        release_at=now + timedelta(hours=6),
        duration_source="standard_default",
        status="ready",
    )
    session.add(task)
    session.flush()

    return property_id, reservation_id, task_id


class TestGuestDepartureMessageClassification:
    """Tests for checkout_policy departure_message function."""

    def test_media_only_messages_skipped(self, session: Session) -> None:
        """Messages with only media (no text) should return 'unknown'.

        Mutation: Change departure_message to treat empty body as 'departed'.
        Fails: assert departure_message("") == "unknown"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("") == "unknown"
        assert departure_message("   ") == "unknown"

    def test_departed_message_classification(self, session: Session) -> None:
        """Messages indicating departure should return 'departed'.

        Mutation: Remove 'checked out' from departure patterns.
        Fails: assert departure_message("We have checked out.") == "departed"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("We have checked out.") == "departed"
        assert departure_message("I've just left the house.") == "departed"
        assert departure_message("We left.") == "departed"
        assert departure_message("Ya salimos.") == "departed"

    def test_still_present_message_classification(self, session: Session) -> None:
        """Messages indicating presence should return 'still_present'.

        Mutation: Remove presence pattern from regex.
        Fails: assert departure_message("We are still here") == "still_present"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("We are still here") == "still_present"
        assert departure_message("I'm still inside") == "still_present"
        assert departure_message("We're still in the house") == "still_present"

    def test_ambiguous_message_classification(self, session: Session) -> None:
        """Unrelated messages should return 'unknown'.

        Mutation: Default to 'departed' instead of 'unknown' at checkout_policy.py:79.
        Fails: assert departure_message("Thanks for everything") == "unknown"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("Thanks for everything") == "unknown"
        assert departure_message("What's the wifi password?") == "unknown"

    def test_quoted_text_ignored(self, session: Session) -> None:
        """Messages with quoted/forwarded text should return 'unknown'.

        Mutation: Remove quote detection at checkout_policy.py:27-28.
        Fails: assert departure_message("He said we are still here") == "unknown"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("He said we are still here") == "unknown"
        assert departure_message("> We have checked out") == "unknown"

    def test_oversized_body_ignored(self, session: Session) -> None:
        """Messages exceeding 4000 chars should return 'unknown'.

        Mutation: Remove length check at checkout_policy.py:23.
        Fails: assert departure_message("We have checked out." + " " * 4000) == "unknown"
        """
        from app.services.checkout_policy import departure_message

        assert departure_message("We have checked out." + " " * 4000) == "unknown"


class TestGuestDepartureSyncErrorIsolation:
    """Tests for run_guest_departure_sync error isolation and recording."""

    def test_error_isolation_records_errors_and_continues(self, hosted_settings: Settings, clock: FrozenClock) -> None:
        """run_guest_departure_sync isolates errors per reservation and records error codes.

        Seeds 3 reservations:
        - #1 raises InvalidTag -> unexpected_InvalidTag
        - #2 raises OperationalError -> unexpected_OperationalError
        - #3 returns "We have checked out." -> success, departure applied

        Asserts:
        - All 3 are processed (call_count == 3)
        - #1 has error_code=unexpected_InvalidTag, attempt_count=1, available_at=now+2min
        - #2 has error_code=unexpected_OperationalError, attempt_count=1, available_at=now+2min
        - #3 has no error code, task.confirmed_checkout_at set, departure evidence exists
        - Exception record is opened with code GUEST_MESSAGE_UNEXPECTED_ERROR

        Mutation: Remove try/except around per-reservation loop at guest_departure_sync.py:161.
        Fails: assert call_count == 3
        """
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        external_id_1 = str(uuid.uuid4())
        external_id_2 = str(uuid.uuid4())
        external_id_3 = str(uuid.uuid4())

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as s:
            _create_completed_connection(s, clock, workspace_id, hosted_settings)
            _create_successful_sync_run(s, clock)
            _, res1_id, task1_id = _create_reservation_with_task(s, clock, hosted_settings, external_id_1)
            _, res2_id, task2_id = _create_reservation_with_task(s, clock, hosted_settings, external_id_2)
            _, res3_id, task3_id = _create_reservation_with_task(s, clock, hosted_settings, external_id_3)
            s.commit()

        now = clock.now()
        call_count = 0
        message_sent_at = now

        def mock_guest_messages(external_id: str) -> list[SourceMessage]:
            nonlocal call_count
            call_count += 1
            if external_id == external_id_1:
                raise InvalidTag()
            elif external_id == external_id_2:
                raise OperationalError("test", {}, Exception())
            else:
                return [
                    SourceMessage(
                        external_id=f"msg-{external_id_3}",
                        body="We have checked out.",
                        sent_at=message_sent_at,
                    )
                ]

        mock_client = MagicMock()
        mock_client.guest_messages = mock_guest_messages
        mock_client.close = MagicMock()

        mock_redis = MagicMock()
        mock_redis.set = MagicMock(return_value=True)
        mock_redis.close = MagicMock()

        with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
            with patch("app.services.guest_departure_sync.HospitableReadClient", return_value=mock_client):
                with patch("app.services.guest_departure_sync.redis.Redis.from_url", return_value=mock_redis):
                    result = run_guest_departure_sync(factory, hosted_settings, clock)

        assert call_count == 3, "All 3 reservations should be processed"
        assert result == 1, "One departure should be applied"

        expected_backoff = now + timedelta(minutes=2)

        with factory() as s:
            checkpoint1 = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_poll",
                    OutboxEvent.aggregate_id == res1_id,
                )
            )
            assert checkpoint1 is not None
            assert checkpoint1.last_error_code == "unexpected_InvalidTag"
            assert checkpoint1.attempt_count == 1
            assert checkpoint1.available_at == expected_backoff

            checkpoint2 = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_poll",
                    OutboxEvent.aggregate_id == res2_id,
                )
            )
            assert checkpoint2 is not None
            assert checkpoint2.last_error_code == "unexpected_OperationalError"
            assert checkpoint2.attempt_count == 1
            assert checkpoint2.available_at == expected_backoff

            checkpoint3 = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_poll",
                    OutboxEvent.aggregate_id == res3_id,
                )
            )
            assert checkpoint3 is not None
            assert checkpoint3.last_error_code is None, "Success path should clear error code"

            task3 = s.get(CleaningTask, task3_id)
            assert task3 is not None
            assert task3.confirmed_checkout_at == message_sent_at

            evidence = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_evidence",
                    OutboxEvent.aggregate_id == res3_id,
                )
            )
            assert evidence is not None, "Departure evidence row should exist"
            assert evidence.payload.get("decision") == "departed"

            exception = s.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == "guest-message-unexpected-error",
                    OperationalException.status.in_(("open", "acknowledged")),
                )
            )
            assert exception is not None, "Exception record should be opened"
            assert exception.code == "GUEST_MESSAGE_UNEXPECTED_ERROR"


class TestGuestDepartureRetryBehavior:
    """Tests for guest departure retry behavior."""

    def test_legacy_retry_fetches_retryable_checkpoint(self, hosted_settings: Settings, clock: FrozenClock) -> None:
        """A message_text_unavailable checkpoint is picked up for retry; provider_unavailable is not.

        Seeds two checkpoints with available_at 30 minutes in the future:
        - message_text_unavailable: should be fetched (retryable)
        - provider_unavailable: should NOT be fetched (not retryable)

        Asserts:
        - Client is called exactly once (for the retryable reservation only)
        - message_text_unavailable checkpoint has error code cleared
        - provider_unavailable checkpoint is unchanged

        Mutation: Remove message_text_unavailable from OR clause at guest_departure_sync.py:69.
        Fails: assert mock_client.guest_messages.call_count == 1
        """
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        external_id_retryable = str(uuid.uuid4())
        external_id_non_retryable = str(uuid.uuid4())

        factory = session_factory(hosted_settings, workspace_id)
        now = clock.now()
        future = now + timedelta(minutes=30)

        with factory() as s:
            _create_completed_connection(s, clock, workspace_id, hosted_settings)
            _create_successful_sync_run(s, clock)

            _, res_retryable_id, _ = _create_reservation_with_task(s, clock, hosted_settings, external_id_retryable)
            _, res_non_retryable_id, _ = _create_reservation_with_task(
                s, clock, hosted_settings, external_id_non_retryable
            )

            retryable_checkpoint = OutboxEvent(
                id=uuid.uuid4(),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(minutes=10),
                version=2,
                topic="hospitable.guest_departure_poll",
                aggregate_id=res_retryable_id,
                aggregate_version=1,
                intent_key=f"guest-departure-poll:{res_retryable_id}",
                payload={},
                available_at=future,
                attempt_count=1,
                last_error_code="message_text_unavailable",
            )
            s.add(retryable_checkpoint)

            non_retryable_checkpoint = OutboxEvent(
                id=uuid.uuid4(),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(minutes=10),
                version=2,
                topic="hospitable.guest_departure_poll",
                aggregate_id=res_non_retryable_id,
                aggregate_version=1,
                intent_key=f"guest-departure-poll:{res_non_retryable_id}",
                payload={},
                available_at=future,
                attempt_count=1,
                last_error_code="provider_unavailable",
            )
            s.add(non_retryable_checkpoint)
            s.commit()

        mock_client = MagicMock()
        mock_client.guest_messages = MagicMock(return_value=[])
        mock_client.close = MagicMock()

        mock_redis = MagicMock()
        mock_redis.set = MagicMock(return_value=True)
        mock_redis.close = MagicMock()

        with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
            with patch("app.services.guest_departure_sync.HospitableReadClient", return_value=mock_client):
                with patch("app.services.guest_departure_sync.redis.Redis.from_url", return_value=mock_redis):
                    run_guest_departure_sync(factory, hosted_settings, clock)

        assert mock_client.guest_messages.call_count == 1, "Client should be called once for retryable only"

        with factory() as s:
            retryable = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_poll",
                    OutboxEvent.aggregate_id == res_retryable_id,
                )
            )
            assert retryable is not None
            assert retryable.last_error_code is None, "Retryable checkpoint should have error code cleared (success)"

            non_retryable = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "hospitable.guest_departure_poll",
                    OutboxEvent.aggregate_id == res_non_retryable_id,
                )
            )
            assert non_retryable is not None
            assert non_retryable.last_error_code == "provider_unavailable", (
                "Non-retryable checkpoint should NOT be fetched"
            )
