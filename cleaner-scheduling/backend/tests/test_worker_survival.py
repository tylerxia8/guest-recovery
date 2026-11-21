"""Tests for worker survival and observability fixes (M1, H4, M5, N1-N6).

These are behavioral tests that exercise the real code paths.
Tests are designed to fail on pre-fix code (verified against main branch).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.adapters.errors import ProviderUnavailable
from app.clock import FrozenClock
from app.config import Settings, clear_settings_cache, get_settings
from app.db import catalog_session_factory, session_factory
from app.logging_redaction import HostedFormatter
from app.models import CleaningTask, OperationalException, OutboxEvent, Property, Reservation
from app.models.tables import ReservationSyncRun
from app.models.workspace_connection import WorkspaceConnection
from app.services.guest_departure_sync import run_guest_departure_sync
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

WorkspaceFactories = Callable[[], list[tuple[uuid.UUID, sessionmaker[Session]]]]


def make_failing_factories(call_count: dict[str, int]) -> WorkspaceFactories:
    """Create a factories callable that fails on first call."""

    def failing_factories() -> list[tuple[uuid.UUID, sessionmaker[Session]]]:
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise RuntimeError("Database connection lost")
        return []

    return failing_factories


def make_intermittent_factories(call_count: dict[str, int]) -> WorkspaceFactories:
    """Create a factories callable that fails on first call, succeeds after."""

    def intermittent_factories() -> list[tuple[uuid.UUID, sessionmaker[Session]]]:
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise RuntimeError("Transient connection failure")
        return []

    return intermittent_factories


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=UTC))


@pytest.fixture
def hosted_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings for a hosted workspace with all features enabled.

    Monkeypatches hosted_workspace to return True regardless of app_env.
    """
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
    now = clock.now()
    catalog.execute(
        text("INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, :now)"),
        {"id": workspace_id, "name": "Test Workspace", "now": now},
    )
    catalog.commit()


def _create_completed_workspace_connection(
    session: Session, clock: FrozenClock, workspace_id: uuid.UUID
) -> WorkspaceConnection:
    """Create a WorkspaceConnection with completed_at set (required for guest departure sync)."""
    now = clock.now()
    connection = WorkspaceConnection(
        workspace_id=workspace_id,
        token_ciphertext=b"encrypted-token-placeholder",
        connected_at=now,
        completed_at=now,
    )
    session.add(connection)
    session.flush()
    return connection


def _create_successful_sync_run(session: Session, clock: FrozenClock) -> ReservationSyncRun:
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
    return run


def _create_claimable_reservation(
    session: Session, clock: FrozenClock, property_id: uuid.UUID, index: int = 0
) -> tuple[Reservation, CleaningTask]:
    """Create a reservation with a ready cleaning task (claimable by guest departure sync)."""
    now = clock.now()
    reservation = Reservation(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        external_id=str(uuid.uuid4()),
        property_id=property_id,
        status="active",
        checkin_at=now - timedelta(days=1),
        checkout_at=now + timedelta(hours=10),
        observed_at=now - timedelta(hours=1),
        provider="hospitable",
        timestamp_provenance="source",
        payload_digest=f"test-digest-{index}",
    )
    session.add(reservation)
    session.flush()

    task = CleaningTask(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        source_reservation_id=reservation.id,
        property_id=property_id,
        kind="departure",
        status="ready",
        release_at=now + timedelta(hours=10),
        deadline_at=now + timedelta(hours=14),
        duration_source="standard_default",
    )
    session.add(task)
    session.flush()
    return reservation, task


def _create_test_property(session: Session, clock: FrozenClock) -> Property:
    """Create a test property."""
    now = clock.now()
    prop = Property(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        external_id=f"test-property-{uuid.uuid4()}",
        name="Test Property",
        provider="hospitable",
        timezone="America/New_York",
        location_ciphertext=b"encrypted-address",
        latitude=40.7128,
        longitude=-74.0060,
        geocode_status="resolved",
        bedrooms=2,
        duration_override_minutes=90,
        active=True,
    )
    session.add(prop)
    session.flush()
    return prop


class TestGuestDepartureExceptionHandling:
    """M5/N3/N4: Behavioral tests for exception handling in guest departure sync.

    These tests verify:
    - First reservation failure does not abort the batch (M5)
    - Distinct error codes for expected vs unexpected errors (N3)
    - Expected errors log without traceback, unexpected with traceback (N4)

    Verified to fail on commit f9e9ab2 (pre-M5/N3/N4 fix).
    """

    def test_first_reservation_failure_does_not_abort_second(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the first reservation fails with an unexpected error, the second should still be processed.

        This test MUST fail on b24b093 (main) because RuntimeError escapes the narrow except clause
        and aborts the entire batch. The fix widens the except to catch all exceptions.

        Verified failure on b24b093:
        AssertionError: Both reservations should be processed
        assert 1 == 2
        """
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as session:
            _create_completed_workspace_connection(session, clock, workspace_id)
            _create_successful_sync_run(session, clock)
            prop = _create_test_property(session, clock)
            res1, task1 = _create_claimable_reservation(session, clock, prop.id, index=0)
            res2, task2 = _create_claimable_reservation(session, clock, prop.id, index=1)
            session.commit()

        call_count = {"count": 0}
        processed_ids: list[str] = []

        def mock_apply_messages(
            factory_arg: Any,
            settings_arg: Any,
            clock_arg: Any,
            reservation: Reservation,
            task: CleaningTask,
            checkpoint_id: uuid.UUID,
            ciphertext: bytes,
            messages: list[Any],
        ) -> int:
            processed_ids.append(str(reservation.id))
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise RuntimeError("Unexpected internal error")
            return 0

        with patch("app.services.guest_departure_sync.HospitableReadClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.guest_messages = MagicMock(return_value=[])
            mock_client.close = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
                with patch("app.services.guest_departure_sync._apply_messages", side_effect=mock_apply_messages):
                    with caplog.at_level(logging.ERROR):
                        run_guest_departure_sync(factory, hosted_settings, clock)

        assert call_count["count"] == 2, "Both reservations should be processed"
        assert len(processed_ids) == 2, "Both reservations should be attempted"

        with factory() as session:
            checkpoints = list(
                session.scalars(select(OutboxEvent).where(OutboxEvent.topic == "hospitable.guest_departure_poll"))
            )
            failed = [c for c in checkpoints if c.last_error_code is not None]
            assert len(failed) == 1, "Only one checkpoint should have failed"
            assert failed[0].attempt_count == 1
            assert failed[0].last_error_code == "unexpected_RuntimeError"

        assert "guest_departure.unexpected_error" in caplog.text
        assert "unexpected_RuntimeError" in caplog.text

    def test_unexpected_error_uses_distinct_error_code(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected errors (not AppError/AdapterError) get distinct error code.

        This test verifies N3: unexpected errors use 'unexpected_<ClassName>' prefix.
        """
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as session:
            _create_completed_workspace_connection(session, clock, workspace_id)
            _create_successful_sync_run(session, clock)
            prop = _create_test_property(session, clock)
            _create_claimable_reservation(session, clock, prop.id)
            session.commit()

        def mock_apply_messages(*args: Any, **kwargs: Any) -> int:
            raise RuntimeError("Unexpected internal error")

        with patch("app.services.guest_departure_sync.HospitableReadClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.guest_messages = MagicMock(return_value=[])
            mock_client.close = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
                with patch("app.services.guest_departure_sync._apply_messages", side_effect=mock_apply_messages):
                    with caplog.at_level(logging.ERROR):
                        run_guest_departure_sync(factory, hosted_settings, clock)

        with factory() as session:
            checkpoint = session.scalar(
                select(OutboxEvent).where(OutboxEvent.topic == "hospitable.guest_departure_poll")
            )
            assert checkpoint is not None
            assert checkpoint.last_error_code == "unexpected_RuntimeError"

            exception = session.scalar(
                select(OperationalException).where(OperationalException.dedupe_key == "guest-message-unexpected-error")
            )
            assert exception is not None
            assert exception.code == "GUEST_MESSAGE_UNEXPECTED_ERROR"
            assert exception.severity == "critical"

        assert "guest_departure.unexpected_error" in caplog.text
        assert "unexpected_RuntimeError" in caplog.text


class TestWorkerLoggingObservability:
    """M1/N4: Exception class names and exc_info logging behavior.

    Verified to fail on commit f9e9ab2 (pre-logging fix).
    """

    def test_expected_adapter_error_no_traceback(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Expected adapter errors should NOT include full traceback (N4)."""
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as session:
            _create_completed_workspace_connection(session, clock, workspace_id)
            _create_successful_sync_run(session, clock)
            prop = _create_test_property(session, clock)
            _create_claimable_reservation(session, clock, prop.id)
            session.commit()

        def mock_apply_messages(*args: Any, **kwargs: Any) -> int:
            raise ProviderUnavailable("Hospitable connection failed.")

        with patch("app.services.guest_departure_sync.HospitableReadClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.guest_messages = MagicMock(return_value=[])
            mock_client.close = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
                with patch("app.services.guest_departure_sync._apply_messages", side_effect=mock_apply_messages):
                    with caplog.at_level(logging.WARNING):
                        run_guest_departure_sync(factory, hosted_settings, clock)

        assert "guest_departure.read_unavailable" in caplog.text
        assert "ProviderUnavailable" in caplog.text
        assert "Traceback" not in caplog.text

    def test_unexpected_error_includes_traceback(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected errors SHOULD include full traceback (N4)."""
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as session:
            _create_completed_workspace_connection(session, clock, workspace_id)
            _create_successful_sync_run(session, clock)
            prop = _create_test_property(session, clock)
            _create_claimable_reservation(session, clock, prop.id)
            session.commit()

        def mock_apply_messages(*args: Any, **kwargs: Any) -> int:
            raise RuntimeError("Unexpected internal error")

        with patch("app.services.guest_departure_sync.HospitableReadClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.guest_messages = MagicMock(return_value=[])
            mock_client.close = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
                with patch("app.services.guest_departure_sync._apply_messages", side_effect=mock_apply_messages):
                    with caplog.at_level(logging.ERROR):
                        run_guest_departure_sync(factory, hosted_settings, clock)

        assert "guest_departure.unexpected_error" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "Traceback" in caplog.text


class TestExceptionAutoResolution:
    """Tests that operational exceptions are auto-resolved when reads recover."""

    def test_unexpected_error_exception_resolved_on_recovery(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The guest-message-unexpected-error exception is resolved when reads recover.

        This test verifies guest_departure_sync.py:113-129 resolves both dedupe keys.
        """
        workspace_id = uuid.uuid4()

        with catalog_session_factory(hosted_settings)() as catalog:
            _create_workspace(catalog, clock, workspace_id)

        factory = session_factory(hosted_settings, workspace_id)
        with factory() as session:
            _create_completed_workspace_connection(session, clock, workspace_id)
            _create_successful_sync_run(session, clock)
            prop = _create_test_property(session, clock)
            _create_claimable_reservation(session, clock, prop.id)
            exception = OperationalException(
                id=uuid.uuid4(),
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                code="GUEST_MESSAGE_UNEXPECTED_ERROR",
                severity="critical",
                dedupe_key="guest-message-unexpected-error",
                status="open",
                opened_at=clock.now(),
                details={"error": "unexpected_RuntimeError"},
            )
            session.add(exception)
            session.commit()

        def mock_apply_messages(*args: Any, **kwargs: Any) -> int:
            return 0

        with patch("app.services.guest_departure_sync.HospitableReadClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.guest_messages = MagicMock(return_value=[])
            mock_client.close = MagicMock()
            mock_client_class.return_value = mock_client

            with patch("app.services.guest_departure_sync.reveal_text", return_value="fake-token"):
                with patch("app.services.guest_departure_sync._apply_messages", side_effect=mock_apply_messages):
                    with caplog.at_level(logging.INFO):
                        run_guest_departure_sync(factory, hosted_settings, clock)

        with factory() as session:
            resolved = session.scalar(
                select(OperationalException).where(
                    OperationalException.dedupe_key == "guest-message-unexpected-error",
                    OperationalException.status == "resolved",
                )
            )
            assert resolved is not None, "Exception should be auto-resolved on successful read"
            assert resolved.resolution_reason == "Current guest message reads recovered."

        assert "guest_departure.read_recovered" in caplog.text


class TestFactoriesFailedRecovery:
    """Tests for factories() failure recovery in worker loops.

    These tests verify that when factories() raises, the worker loop:
    1. Logs the failure with traceback
    2. Continues on the next iteration instead of crashing

    This MUST fail on b24b093 (main) because factories() was called inline
    without try/except, so any exception would propagate and crash the loop.

    After the fix (local_hospitable.py:68-73, :95-100, :190-195), factories()
    failures are caught, logged with factories_failed, and the loop continues.
    """

    def test_sync_workspaces_factories_failed_continues(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When factories() raises, sync_workspaces logs and returns instead of crashing.

        On b24b093, this would crash because factories() was called inline.
        The fix wraps it in try/except and logs hospitable_sync.factories_failed.
        """
        from app.local_hospitable import sync_workspaces

        call_count: dict[str, int] = {"count": 0}
        factories = make_failing_factories(call_count)

        with caplog.at_level(logging.ERROR):
            sync_workspaces(factories, hosted_settings, clock)

        assert call_count["count"] == 1, "factories was called once"
        assert "hospitable_sync.factories_failed" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "Traceback" in caplog.text

    def test_automate_emails_factories_failed_continues(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When factories() raises, automate_emails logs and returns instead of crashing.

        On b24b093, this would crash because factories() was called inline.
        The fix wraps it in try/except and logs email_automation.factories_failed.
        """
        from app.local_hospitable import automate_emails

        call_count: dict[str, int] = {"count": 0}
        factories = make_failing_factories(call_count)

        with caplog.at_level(logging.ERROR):
            automate_emails(factories, hosted_settings, clock)

        assert call_count["count"] == 1, "factories was called once"
        assert "email_automation.factories_failed" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "Traceback" in caplog.text

    def test_sync_guest_departures_factories_failed_continues(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When factories() raises, sync_guest_departures logs and returns instead of crashing.

        On b24b093, this would crash because factories() was called inline.
        The fix wraps it in try/except and logs guest_departure.factories_failed.
        """
        from app.local_hospitable import sync_guest_departures

        call_count: dict[str, int] = {"count": 0}
        factories = make_failing_factories(call_count)

        with caplog.at_level(logging.ERROR):
            sync_guest_departures(factories, hosted_settings, clock)

        assert call_count["count"] == 1, "factories was called once"
        assert "guest_departure.factories_failed" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "Traceback" in caplog.text

    def test_sync_workspaces_processes_second_call_after_first_fails(
        self, hosted_settings: Settings, clock: FrozenClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The second sync call should still work after the first factories() fails.

        This simulates the worker loop behavior where failures don't block future iterations.
        """
        from app.local_hospitable import sync_workspaces

        call_count: dict[str, int] = {"count": 0}
        factories = make_intermittent_factories(call_count)

        with caplog.at_level(logging.ERROR):
            sync_workspaces(factories, hosted_settings, clock)
            sync_workspaces(factories, hosted_settings, clock)

        assert call_count["count"] == 2, "factories was called twice"
        assert "hospitable_sync.factories_failed" in caplog.text


class TestHostedFormatterSafeFallback:
    """N5: HostedFormatter.format must fall back safely on errors."""

    def test_format_error_returns_safe_fallback(self) -> None:
        """When format() raises, return a safe redacted line."""
        formatter = HostedFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        class BrokenMessage:
            def __str__(self) -> str:
                raise ValueError("Cannot stringify")

        record.msg = BrokenMessage()

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["message"] == "[formatting error - message redacted]"
        assert parsed["format_error"] is True
        assert "level" in parsed
        assert "timestamp" in parsed

    def test_normal_format_still_works(self) -> None:
        """Normal formatting should still work correctly."""
        formatter = HostedFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Normal message with %s",
            args=("placeholder",),
            exc_info=None,
        )

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["message"] == "Normal message with placeholder"
        assert parsed["logger"] == "test.logger"
        assert parsed["level"] == "info"
        assert "format_error" not in parsed


class TestDepartureWorkerSurvival:
    """Tests for departure_worker outer try/except at local_hospitable.py:264-270.

    The departure_worker runs sync_guest_departures in a loop. If it raises,
    the outer try/except catches the error, logs it, and continues the next tick.
    """

    def test_sync_departures_error_is_logged_and_continues(self, caplog: pytest.LogCaptureFixture) -> None:
        """When sync_guest_departures raises, the error is logged and loop continues.

        This tests the outer try/except at local_hospitable.py:262-267.
        We patch the 60s timeout to 0.01s so the loop ticks multiple times quickly.

        Mutation: Remove try/except around sync_departures call at :262-267.
        Fails: The exception would propagate and kill the worker on first call.
        """
        import asyncio

        from app.local_hospitable import lifespan

        class MockApp:
            def __init__(self) -> None:
                self.state = MagicMock()
                self.state.settings = get_settings().model_copy(
                    update={
                        "hospitable_guest_departure_enabled": True,
                        "local_email_automation_enabled": False,
                        "security_alert_email_enabled": False,
                    }
                )
                self.state.clock = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=UTC))
                self.state.session_factory = MagicMock()
                self.state.workspace_factories = {}
                self.state.wake_automation = lambda: None

        call_count = {"count": 0}

        def mock_sync_guest_departures(_factories: object, _settings: object, _clock: object, _wake: object) -> None:
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise RuntimeError("Test departure sync failure")

        original_wait_for = asyncio.wait_for

        async def fast_wait_for(coro: Any, timeout: float) -> Any:
            return await original_wait_for(coro, timeout=0.01)

        async def run_test() -> None:
            app: Any = MockApp()

            with patch("app.local_hospitable.sync_guest_departures", mock_sync_guest_departures):
                with patch("app.local_hospitable.sync_workspaces"):
                    with patch("app.local_hospitable.automate_emails"):
                        with patch("app.local_hospitable.drain_security_alerts"):
                            with patch("asyncio.wait_for", fast_wait_for):
                                async with lifespan(app):
                                    await asyncio.sleep(0.05)

        with caplog.at_level(logging.ERROR):
            asyncio.run(run_test())

        assert call_count["count"] >= 2, f"sync_guest_departures should run at least twice, got {call_count['count']}"
        assert "guest_departure.worker_failed" in caplog.text, (
            "Should log guest_departure.worker_failed when sync_departures raises"
        )
        assert "RuntimeError" in caplog.text
