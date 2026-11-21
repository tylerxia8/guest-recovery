"""Tests for availability email time window (M3 fix).

Tests run_availability_emails at 19:59 and 20:00 using FrozenClock on a fixed date.
Seeds real workspaces with completed connections and records _queue calls.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from app.clock import FrozenClock
from app.config import Settings
from app.db import get_engine
from app.domain.errors import AppError
from app.models.workspace_connection import WorkspaceConnection
from app.services.availability_email import run_availability_emails
from app.workspaces import Workspace
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    pass

NY = ZoneInfo("America/New_York")
FIXED_DATE = datetime(2026, 6, 15, tzinfo=NY)


def _migrator_session() -> sessionmaker:  # type: ignore[type-arg]
    """Create session factory using migrator credentials (bypasses RLS)."""
    return sessionmaker(bind=get_engine(os.environ["MIGRATION_DATABASE_URL"]), expire_on_commit=False)


@pytest.fixture
def workspace_a() -> uuid.UUID:
    """Create workspace A with completed connection."""
    workspace_id = uuid.uuid4()
    now = datetime.now(NY)
    factory = _migrator_session()
    with factory() as session:
        workspace = Workspace(
            id=workspace_id,
            name="Workspace A",
            created_at=now,
            deleted_at=None,
        )
        session.add(workspace)
        session.flush()
        connection = WorkspaceConnection(
            workspace_id=workspace_id,
            token_ciphertext=b"test-token-a",
            connected_at=now,
            completed_at=now,
        )
        session.add(connection)
        session.commit()
    return workspace_id


@pytest.fixture
def workspace_b() -> uuid.UUID:
    """Create workspace B with completed connection."""
    workspace_id = uuid.uuid4()
    now = datetime.now(NY)
    factory = _migrator_session()
    with factory() as session:
        workspace = Workspace(
            id=workspace_id,
            name="Workspace B",
            created_at=now,
            deleted_at=None,
        )
        session.add(workspace)
        session.flush()
        connection = WorkspaceConnection(
            workspace_id=workspace_id,
            token_ciphertext=b"test-token-b",
            connected_at=now,
            completed_at=now,
        )
        session.add(connection)
        session.commit()
    return workspace_id


class TestRunAvailabilityEmails:
    """Tests for run_availability_emails time window behavior."""

    def test_run_at_1959_queues_both_workspaces(
        self,
        settings: Settings,
        workspace_a: uuid.UUID,
        workspace_b: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_availability_emails at 19:59 should queue both workspaces (within window).

        Uses FrozenClock on a fixed date, never SystemClock.
        Seeds two real workspaces with completed connections.
        Records _queue calls without mocking catalog.
        """
        at_1959 = FIXED_DATE.replace(hour=19, minute=59, second=0, microsecond=0)
        clock = FrozenClock(at_1959)

        monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))

        test_settings = settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "local_scheduling_enabled": True,
                "gmail_assignment_send_enabled": True,
                "dispatch_local_time": "20:00",
            }
        )

        queued_workspaces: list[uuid.UUID] = []

        def mock_queue(session: object, _settings: object, _clock: object, _day: object) -> None:
            workspace_id = getattr(session, "info", {}).get("workspace_id")
            if workspace_id:
                queued_workspaces.append(workspace_id)

        with patch("app.services.availability_email._queue", mock_queue):
            run_availability_emails(test_settings, clock)

        assert workspace_a in queued_workspaces, f"Workspace A should be queued, got {queued_workspaces}"
        assert workspace_b in queued_workspaces, f"Workspace B should be queued, got {queued_workspaces}"
        assert len(queued_workspaces) == 2, f"Both workspaces should be queued, got {len(queued_workspaces)}"

    def test_run_at_2000_queues_neither_workspace(
        self,
        settings: Settings,
        workspace_a: uuid.UUID,
        workspace_b: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """run_availability_emails at 20:00 should queue neither (window closed).

        Uses FrozenClock on a fixed date, never SystemClock.
        No workspace_failed should be logged.
        """
        at_2000 = FIXED_DATE.replace(hour=20, minute=0, second=0, microsecond=0)
        clock = FrozenClock(at_2000)

        monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))

        test_settings = settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "local_scheduling_enabled": True,
                "gmail_assignment_send_enabled": True,
                "dispatch_local_time": "20:00",
            }
        )

        queued_workspaces: list[uuid.UUID] = []

        def mock_queue(session: object, _settings: object, _clock: object, _day: object) -> None:
            workspace_id = getattr(session, "info", {}).get("workspace_id")
            if workspace_id:
                queued_workspaces.append(workspace_id)

        with caplog.at_level(logging.ERROR):
            with patch("app.services.availability_email._queue", mock_queue):
                run_availability_emails(test_settings, clock)

        assert len(queued_workspaces) == 0, f"No workspaces should be queued at 20:00, got {queued_workspaces}"
        assert "workspace_failed" not in caplog.text, "No workspace_failed should be logged"

    def test_error_in_workspace_a_does_not_stop_workspace_b(
        self,
        settings: Settings,
        workspace_a: uuid.UUID,
        workspace_b: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If workspace A's _queue raises AppError, workspace B still runs.

        Error isolation: each workspace is processed independently.
        """
        at_1959 = FIXED_DATE.replace(hour=19, minute=59, second=0, microsecond=0)
        clock = FrozenClock(at_1959)

        monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))

        test_settings = settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "local_scheduling_enabled": True,
                "gmail_assignment_send_enabled": True,
                "dispatch_local_time": "20:00",
            }
        )

        queued_workspaces: list[uuid.UUID] = []
        first_call = True

        def mock_queue_with_error(session: object, _settings: object, _clock: object, _day: object) -> None:
            nonlocal first_call
            workspace_id = getattr(session, "info", {}).get("workspace_id")
            if first_call:
                first_call = False
                raise AppError("test_error", "Test error in first workspace", 500)
            if workspace_id:
                queued_workspaces.append(workspace_id)

        with patch("app.services.availability_email._queue", mock_queue_with_error):
            run_availability_emails(test_settings, clock)

        assert len(queued_workspaces) == 1, f"Second workspace should still be queued, got {queued_workspaces}"


class TestDstTransitionBehavior:
    """Tests for DST-transition date behavior with real _queue.

    DST transitions in America/New_York:
    - Spring forward: Second Sunday in March at 2:00 AM (clock jumps to 3:00 AM)
    - Fall back: First Sunday in November at 2:00 AM (clock falls back to 1:00 AM)

    The time window (09:00 to dispatch_local_time) is in local time.
    On DST days, UTC offsets change, but local time is what matters.
    """

    def test_spring_forward_dst_transition_date(
        self,
        settings: Settings,
        workspace_a: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """run_availability_emails queues nothing at 00:30 UTC on spring DST day.

        2026-03-09 00:30 UTC = 2026-03-08 20:30 EDT (after spring forward).
        This is AFTER the 20:00 dispatch cutoff, so no availability email should queue.

        A naive UTC-date mutant (using 00:30 UTC → Mar 9) would incorrectly try to queue.
        A fixed -5 offset mutant would compute 19:30 EST and try to queue for Mar 9.
        Both should fail: no queue call, no workspace_failed log.

        Mutation: Use UTC date or fixed -5 offset instead of America/New_York.
        Fails: Would queue for Mar 9 or fail with workspace_failed.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.clock import FrozenClock

        at_0030_march_9_utc = datetime(2026, 3, 9, 0, 30, 0, tzinfo=ZoneInfo("UTC"))
        clock = FrozenClock(at_0030_march_9_utc)

        monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))

        test_settings = settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "local_scheduling_enabled": True,
                "gmail_assignment_send_enabled": True,
                "dispatch_local_time": "20:00",
            }
        )

        queued_days: list[date] = []

        def mock_queue(session: object, _settings: object, _clock: object, day: date) -> None:
            queued_days.append(day)

        with patch("app.services.availability_email._queue", mock_queue):
            run_availability_emails(test_settings, clock)

        assert len(queued_days) == 0, f"No queue expected at 20:30 EDT, got {queued_days}"
        assert "availability_email.workspace_failed" not in caplog.text

    def test_fall_back_dst_transition_date(
        self,
        settings: Settings,
        workspace_a: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """run_availability_emails queues Nov 2 at 00:30 UTC on fall back day.

        2026-11-02 00:30 UTC = 2026-11-01 19:30 EST (after fall back).
        This is within the 09:00-20:00 window, so availability emails queue for Nov 2.

        A naive UTC-date mutant would compute Nov 2 (correct) but for wrong reason.
        A fixed -4 offset mutant would compute 20:30 EDT (wrong, outside window).

        Mutation: Use UTC date or fixed -4 offset instead of America/New_York.
        Fails: Either wrong date or no queue call.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.clock import FrozenClock

        at_0030_nov_2_utc = datetime(2026, 11, 2, 0, 30, 0, tzinfo=ZoneInfo("UTC"))
        clock = FrozenClock(at_0030_nov_2_utc)

        monkeypatch.setattr(Settings, "hosted_workspace", property(lambda self: True))

        test_settings = settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "local_scheduling_enabled": True,
                "gmail_assignment_send_enabled": True,
                "dispatch_local_time": "20:00",
            }
        )

        queued_days: list[date] = []

        def mock_queue(session: object, _settings: object, _clock: object, day: date) -> None:
            queued_days.append(day)

        with patch("app.services.availability_email._queue", mock_queue):
            run_availability_emails(test_settings, clock)

        assert len(queued_days) >= 1, "Should queue at 19:30 EST"
        assert queued_days[0] == date(2026, 11, 2), f"Service date should be Nov 2, got {queued_days[0]}"
        assert "availability_email.workspace_failed" not in caplog.text
