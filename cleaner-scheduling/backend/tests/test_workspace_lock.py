"""Behavior tests for workspace-scoped advisory locks (H1, M2).

These tests verify lock behavior against the real CI Postgres database:
- Same workspace locks block each other
- Different workspaces don't block each other
- Locks are not held during long-running operations (solver, SMTP)
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from app.api.routes.onboarding import onboarding_token
from app.config import get_settings
from app.db import catalog_session_factory, session_factory
from app.services.workspace_lock import (
    SCHEDULE_PUBLICATION_LOCK,
    acquire_workspace_lock,
    get_workspace_id,
    try_acquire_workspace_lock,
)
from app.workspaces import LEGACY_WORKSPACE_ID
from sqlalchemy import select


class TestGetWorkspaceId:
    """Tests for workspace ID extraction from session."""

    def test_returns_workspace_id_from_session_info(self) -> None:
        """get_workspace_id returns workspace_id from session.info when present."""
        workspace_id = uuid.uuid4()
        session = MagicMock()
        session.info = {"workspace_id": workspace_id}

        result = get_workspace_id(session)

        assert result == workspace_id

    def test_raises_when_workspace_id_missing(self) -> None:
        """get_workspace_id raises ValueError when workspace_id is not in session.info."""
        session = MagicMock()
        session.info = {}

        with pytest.raises(ValueError, match="Session missing workspace_id"):
            get_workspace_id(session)

    def test_raises_for_invalid_type(self) -> None:
        """get_workspace_id raises ValueError when workspace_id is not a UUID."""
        session = MagicMock()
        session.info = {"workspace_id": "not-a-uuid"}

        with pytest.raises(ValueError, match="Session workspace_id must be a UUID"):
            get_workspace_id(session)


class TestSameWorkspaceBlocking:
    """Tests that the same workspace locks block each other."""

    def test_try_lock_returns_false_when_same_workspace_holds_lock(self) -> None:
        """When one session holds a lock, another session's try-lock returns False."""
        settings = get_settings()
        factory = session_factory(settings, workspace_id=LEGACY_WORKSPACE_ID)
        lock_acquired_in_other = threading.Event()
        try_lock_result: list[bool] = []

        def hold_lock() -> None:
            with factory() as session:
                acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, SCHEDULE_PUBLICATION_LOCK)
                lock_acquired_in_other.set()
                time.sleep(0.5)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(hold_lock)
            lock_acquired_in_other.wait(timeout=5)
            time.sleep(0.1)

            with factory() as session:
                result = try_acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, SCHEDULE_PUBLICATION_LOCK)
                try_lock_result.append(result)

            future.result(timeout=5)

        assert try_lock_result[0] is False

    def test_blocking_lock_waits_for_same_workspace(self) -> None:
        """acquire_workspace_lock blocks until the other session releases."""
        settings = get_settings()
        factory = session_factory(settings, workspace_id=LEGACY_WORKSPACE_ID)
        lock_acquired_first = threading.Event()
        lock_released_first = threading.Event()
        second_lock_acquired_time: list[float] = []

        def hold_lock_briefly() -> None:
            with factory() as session:
                acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, SCHEDULE_PUBLICATION_LOCK)
                lock_acquired_first.set()
                time.sleep(0.3)
                session.commit()
            lock_released_first.set()

        def wait_for_lock() -> None:
            lock_acquired_first.wait(timeout=5)
            time.sleep(0.05)
            with factory() as session:
                acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, SCHEDULE_PUBLICATION_LOCK)
                second_lock_acquired_time.append(time.monotonic())
                session.commit()

        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(hold_lock_briefly)
            f2 = executor.submit(wait_for_lock)
            f1.result(timeout=5)
            f2.result(timeout=5)

        assert len(second_lock_acquired_time) == 1
        assert second_lock_acquired_time[0] - start >= 0.25


class TestDifferentWorkspaceIsolation:
    """Tests that different workspaces don't block each other.

    These tests use catalog_session_factory (no workspace_id) and pass workspace_id
    directly to the lock functions. This avoids the after_begin event that checks
    if the workspace exists in the database.
    """

    def test_different_workspaces_can_hold_locks_simultaneously(self) -> None:
        """Two different workspaces can both hold the same lock type at once."""
        settings = get_settings()
        workspace1 = uuid.uuid4()
        workspace2 = uuid.uuid4()
        factory = catalog_session_factory(settings)
        ws1_acquired = threading.Event()
        ws2_try_result: list[bool] = []

        def hold_workspace1_lock() -> None:
            with factory() as session:
                acquire_workspace_lock(session, workspace1, SCHEDULE_PUBLICATION_LOCK)
                ws1_acquired.set()
                time.sleep(0.5)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(hold_workspace1_lock)
            ws1_acquired.wait(timeout=5)
            time.sleep(0.1)

            with factory() as session:
                result = try_acquire_workspace_lock(session, workspace2, SCHEDULE_PUBLICATION_LOCK)
                ws2_try_result.append(result)

            future.result(timeout=5)

        assert ws2_try_result[0] is True

    def test_blocking_lock_does_not_wait_for_different_workspace(self) -> None:
        """acquire_workspace_lock returns immediately for a different workspace."""
        settings = get_settings()
        workspace1 = uuid.uuid4()
        workspace2 = uuid.uuid4()
        factory = catalog_session_factory(settings)
        ws1_acquired = threading.Event()
        ws2_lock_time: list[float] = []

        def hold_workspace1_lock() -> None:
            with factory() as session:
                acquire_workspace_lock(session, workspace1, SCHEDULE_PUBLICATION_LOCK)
                ws1_acquired.set()
                time.sleep(0.5)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(hold_workspace1_lock)
            ws1_acquired.wait(timeout=5)

            start = time.monotonic()
            with factory() as session:
                acquire_workspace_lock(session, workspace2, SCHEDULE_PUBLICATION_LOCK)
                ws2_lock_time.append(time.monotonic() - start)
                session.commit()

            future.result(timeout=5)

        assert ws2_lock_time[0] < 0.1


class TestLockNotHeldDuringLongOperations:
    """Tests that locks are released before long-running I/O operations.

    These tests verify that production code releases the advisory lock before:
    - SMTP I/O in _drain
    - Solver execution in run_email_automation

    Each test patches the external call and verifies that another thread can
    acquire the lock during that call.

    These tests FAIL on pre-fix code (commit 55669e0) where SMTP ran inside
    the locked transaction block.
    """

    def test_lock_released_before_smtp_in_drain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify lock is released before SMTP I/O in _drain.

        Seeds fixtures and an EmailDelivery in 'queued' status, then patches connect() to:
        1. Signal that SMTP has started (meaning claim phase committed)
        2. Wait for the test to try acquiring the lock
        3. Return a mock client

        The test thread tries to acquire the lock during the SMTP window.
        If _drain held the lock during SMTP, this would fail.

        MUST fail on 55669e0 where SMTP ran inside the locked transaction block.
        """
        import hashlib
        import json
        from datetime import datetime, timedelta
        from unittest.mock import MagicMock, patch
        from zoneinfo import ZoneInfo

        from app.clock import FrozenClock
        from app.models import (
            Cleaner,
            CleanerAccessLink,
            CleanerWeeklyHours,
            EmailDelivery,
            Property,
        )
        from app.services.email_automation import _drain
        from app.services.field_cipher import protect_text

        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)
        clock = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=ZoneInfo("America/New_York")))

        with factory() as session:
            prop = session.scalars(select(Property).limit(1)).first()
            assert prop is not None, "Need seeded property"
            cleaner_id = uuid.uuid4()
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Test Cleaner",
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

            test_fingerprint = "test-fingerprint-smtp"
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
                message_key=f"test-onboarding-{delivery_id}",
                fingerprint=test_fingerprint,
                status="queued",
                next_attempt_at=clock.now() - timedelta(minutes=1),
                attempt_count=0,
                snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
            )
            session.add(delivery)
            session.commit()

        smtp_started = threading.Event()
        lock_check_done = threading.Event()
        lock_acquired_during_smtp: list[bool] = []

        def mock_connect(creds_file: str) -> tuple[MagicMock, str]:
            smtp_started.set()
            lock_check_done.wait(timeout=5)
            mock_client = MagicMock()
            mock_client.send_message.return_value = {}  # empty dict = success
            return mock_client, "sender@example.com"

        def mock_onboarding_preview(*args: object, **kwargs: object) -> dict[str, object]:
            return {
                "recipient": "test@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
                "fingerprint": test_fingerprint,
            }

        def run_drain() -> None:
            with (
                patch("app.services.email_automation.connect", mock_connect),
                patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview),
            ):
                _drain(factory, settings, clock, kinds=("onboarding",))

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(run_drain)
            assert smtp_started.wait(timeout=10), "SMTP should start (claim phase should commit)"
            time.sleep(0.05)

            with factory() as session:
                result = try_acquire_workspace_lock(session, workspace_id, SCHEDULE_PUBLICATION_LOCK)
                lock_acquired_during_smtp.append(result)
            lock_check_done.set()
            future.result(timeout=15)

        assert lock_acquired_during_smtp[0] is True, "Lock should be acquirable during SMTP I/O"

        with factory() as session:
            final_row = session.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "accepted", f"Row should end accepted, got {final_row.status}"

    def test_lock_released_before_solver_in_email_automation(self) -> None:
        """Verify lock is released before solver in run_email_automation.

        Patches solve() to:
        1. Signal that solver has started (meaning prep phase committed)
        2. Wait for the test to try acquiring the lock
        3. Return a minimal valid result

        The test thread tries to acquire the lock during the solver window.
        If run_email_automation held the lock during solver, this would fail.

        MUST fail on commit 7b89f95 (pre-split-transaction fix):
        AssertionError: Lock should be acquirable during solver execution
        """
        from datetime import datetime, timedelta
        from unittest.mock import MagicMock, patch
        from zoneinfo import ZoneInfo

        from app.clock import FrozenClock
        from app.domain.scheduling import SolveResult
        from app.models import (
            Cleaner,
            CleanerEligibility,
            CleanerRate,
            CleaningTask,
            PolicyVersion,
            Property,
            Reservation,
            ScheduleDay,
        )
        from app.services.email_automation import run_email_automation

        base_settings = get_settings()
        settings = base_settings.model_copy(
            update={
                "local_email_automation_enabled": True,
                "hospitable_read_only": True,
                "local_scheduling_enabled": True,
                "local_free_routing": True,  # required for hospitable_read_only
                "reservation_provider": "hospitable",  # required for hospitable_read_only
                "gmail_assignment_send_enabled": True,
                "workspace_signup_enabled": False,
            }
        )
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)
        clock = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=ZoneInfo("America/New_York")))

        with factory() as session:
            policy = session.scalar(select(PolicyVersion).where(PolicyVersion.is_active).limit(1))
            if policy is None:
                policy = PolicyVersion(
                    id=uuid.uuid4(),
                    created_at=clock.now(),
                    updated_at=clock.now(),
                    version=1,
                    version_number=1,
                    settings={},
                    is_active=True,
                    approved_by="test-system",
                    approved_at=clock.now(),
                    effective_at=clock.now(),
                )
                session.add(policy)
                session.flush()

            schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == clock.now().date()))
            if schedule_day is None:
                schedule_day = ScheduleDay(
                    id=uuid.uuid4(),
                    created_at=clock.now(),
                    updated_at=clock.now(),
                    service_date=clock.now().date(),
                    revision=1,
                    policy_version=policy.version_number,
                    active_run_id=None,
                )
                session.add(schedule_day)
                session.flush()

            prop = session.scalars(select(Property).where(Property.active).limit(1)).first()
            assert prop is not None, "Need seeded property"

            cleaner_id = uuid.uuid4()
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Test Cleaner",
                phone_ciphertext=b"encrypted-phone",
                phone_lookup_hmac=f"hmac-{cleaner_id}",
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            session.add(cleaner)
            session.flush()

            rate = CleanerRate(
                id=uuid.uuid4(),
                created_at=clock.now(),
                cleaner_id=cleaner.id,
                property_id=None,
                rule_version=1,
                effective_from=(clock.now() - timedelta(days=30)).date(),
                flat_rate_cents=10000,
                currency="USD",
                created_by="test",
                reason="test rate",
            )
            session.add(rate)

            eligibility = CleanerEligibility(
                id=uuid.uuid4(),
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                cleaner_id=cleaner.id,
                property_id=prop.id,
                eligible=True,
                reason="test eligibility",
            )
            session.add(eligibility)

            reservation = Reservation(
                id=uuid.uuid4(),
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                external_id=f"test-res-solver-{uuid.uuid4()}",
                property_id=prop.id,
                status="active",
                checkin_at=clock.now() - timedelta(days=1),
                checkout_at=clock.now() + timedelta(hours=10),
                observed_at=clock.now() - timedelta(hours=1),
                provider="mock",
                timestamp_provenance="source",
                payload_digest="test-digest-solver",
            )
            session.add(reservation)
            session.flush()

            task = CleaningTask(
                id=uuid.uuid4(),
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                source_reservation_id=reservation.id,
                property_id=prop.id,
                kind="departure",
                status="ready",
                release_at=clock.now(),
                deadline_at=clock.now() + timedelta(hours=4),
                duration_source="standard_default",
            )
            session.add(task)
            session.commit()

        solver_started = threading.Event()
        lock_check_done = threading.Event()
        lock_acquired_during_solver: list[bool] = []

        def mock_solve(jobs: object, shifts: object, travel: object, time_limit_seconds: int = 60) -> SolveResult:
            solver_started.set()
            lock_check_done.wait(timeout=5)
            return SolveResult(
                solver_version="test-1.0",
                solver_status="optimal",
                placed=(),
                uncovered=(),
                coverage=0,
                travel_seconds=0,
                elapsed_seconds=0.1,
                objective={},
            )

        def run_automation() -> None:
            with (
                patch("app.services.schedules.solve", mock_solve),
                patch("app.services.email_automation.publication_blocked", return_value=False),
                patch("app.services.schedules.publication_blocked", return_value=False),
                patch("app.services.schedules.current_local_baseline", return_value=MagicMock(id=uuid.uuid4())),
                patch("app.services.email_automation.current_local_baseline", return_value=MagicMock(id=uuid.uuid4())),
            ):
                run_email_automation(factory, settings, clock)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(run_automation)
            assert solver_started.wait(timeout=10), "Solver should be invoked"
            time.sleep(0.05)

            with factory() as session:
                result = try_acquire_workspace_lock(session, workspace_id, SCHEDULE_PUBLICATION_LOCK)
                lock_acquired_during_solver.append(result)
            lock_check_done.set()
            future.result(timeout=15)

        assert lock_acquired_during_solver[0] is True, "Lock should be acquirable during solver execution"


class TestReservationSyncLock:
    """Tests for the M2 reservation sync try-lock."""

    def test_sync_lock_blocks_same_workspace(self) -> None:
        """Reservation sync try-lock returns False when same workspace holds it."""
        settings = get_settings()
        factory = session_factory(settings, workspace_id=LEGACY_WORKSPACE_ID)
        lock_acquired = threading.Event()
        try_lock_result: list[bool] = []

        def hold_sync_lock() -> None:
            with factory() as session:
                result = try_acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, "reservation-sync")
                if result:
                    lock_acquired.set()
                    time.sleep(0.5)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(hold_sync_lock)
            lock_acquired.wait(timeout=5)
            time.sleep(0.1)

            with factory() as session:
                result = try_acquire_workspace_lock(session, LEGACY_WORKSPACE_ID, "reservation-sync")
                try_lock_result.append(result)

            future.result(timeout=5)

        assert try_lock_result[0] is False

    def test_sync_lock_allows_different_workspaces(self) -> None:
        """Different workspaces can both hold the reservation-sync lock."""
        settings = get_settings()
        workspace1 = uuid.uuid4()
        workspace2 = uuid.uuid4()
        factory = catalog_session_factory(settings)
        ws1_acquired = threading.Event()
        ws2_result: list[bool] = []

        def hold_ws1_sync_lock() -> None:
            with factory() as session:
                result = try_acquire_workspace_lock(session, workspace1, "reservation-sync")
                if result:
                    ws1_acquired.set()
                    time.sleep(0.5)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(hold_ws1_sync_lock)
            ws1_acquired.wait(timeout=5)
            time.sleep(0.1)

            with factory() as session:
                result = try_acquire_workspace_lock(session, workspace2, "reservation-sync")
                ws2_result.append(result)

            future.result(timeout=5)

        assert ws2_result[0] is True


class TestEmailAutomationVersionCheck:
    """Tests for email_automation.py version check fix.

    Verifies that validation errors properly update the row status instead
    of stranding it in 'sending' status.

    MUST fail on 5e47fdb where the version check used claimed_version + 1
    even when validation failed (and thus version wasn't bumped).
    """

    def test_validation_error_marks_row_stale_not_sending(self) -> None:
        """When validation raises schedule_changed, row should end as 'stale' not stuck in 'sending'.

        On 5e47fdb, the version check at :915 was:
            if row.version != claimed_version + 1: continue

        But when validation fails, row.version == claimed_version (not + 1),
        so the check would skip the row, leaving it stranded as 'sending'.

        The fix uses expected_version = claimed_version + 1 if send_params else claimed_version.
        """
        import hashlib
        import json
        from datetime import datetime, timedelta
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from app.clock import FrozenClock
        from app.domain.errors import AppError
        from app.models import Cleaner, CleanerAccessLink, CleanerWeeklyHours, EmailDelivery, Property
        from app.services.email_automation import _drain
        from app.services.field_cipher import protect_text

        settings = get_settings()
        workspace_id = LEGACY_WORKSPACE_ID
        factory = session_factory(settings, workspace_id)
        clock = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=ZoneInfo("America/New_York")))

        with factory() as session:
            prop = session.scalars(select(Property).limit(1)).first()
            assert prop is not None, "Need seeded property"

            cleaner_id = uuid.uuid4()
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Test Cleaner Validation",
                phone_ciphertext=protect_text("+15551234568", settings.field_encryption_secret),
                email_ciphertext=protect_text("validation@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"pending-phone-val:{cleaner_id}".encode()).hexdigest(),
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

            snapshot = {
                "recipient": "validation@example.com",
                "subject": "Complete your onboarding",
                "body": "Please complete your onboarding.",
            }
            fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
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
                message_key=f"test-validation-{delivery_id}",
                fingerprint=fingerprint,
                status="queued",
                next_attempt_at=clock.now() - timedelta(minutes=1),
                attempt_count=0,
                snapshot_ciphertext=protect_text(json.dumps(snapshot), settings.field_encryption_secret),
            )
            session.add(delivery)
            session.commit()

        def mock_onboarding_preview(*args: object, **kwargs: object) -> None:
            raise AppError("schedule_changed", "Scheduling inputs changed before submission.", 409)

        with patch("app.services.email_automation.onboarding_preview", mock_onboarding_preview):
            _drain(factory, settings, clock, kinds=("onboarding",))

        with factory() as session:
            final_row = session.get(EmailDelivery, delivery_id)
            assert final_row is not None
            assert final_row.status == "stale", (
                f"Row should be 'stale' after schedule_changed, got '{final_row.status}'"
            )
            assert final_row.error_code == "schedule_changed"
