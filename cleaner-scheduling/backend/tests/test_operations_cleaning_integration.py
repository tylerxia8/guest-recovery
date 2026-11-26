import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.routes.ingestion import (
    OperationsTouchupWrite,
    operations_cleaning_status,
    operations_cleaning_touchup,
    operations_stays,
)
from app.clock import FrozenClock
from app.config import get_settings
from app.db import get_engine, session_factory
from app.domain.errors import AppError
from app.services import guest_recovery
from sqlalchemy import text


def check_cleaning_handoff_isolation(settings, migrator):
    now = datetime.now(UTC)
    workspace_a, workspace_b = uuid4(), uuid4()
    booking, property_source, request = str(uuid4()), str(uuid4()), str(uuid4())
    with migrator.begin() as connection:
        for index, workspace in enumerate((workspace_a, workspace_b)):
            prop = uuid4()
            connection.execute(
                text("INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, 'Fixture Workspace')"),
                {"id": workspace, "now": now},
            )
            connection.execute(
                text("""
                    INSERT INTO properties
                    (id, workspace_id, created_at, updated_at, version, provider, external_id, name, bedrooms,
                     location_ciphertext, geocode_status, active)
                    VALUES (:id, :workspace, :now, :now, 1, 'hospitable', :source, :name, 2,
                            :location, 'unavailable', true)
                """),
                {
                    "id": prop,
                    "workspace": workspace,
                    "now": now,
                    "source": property_source,
                    "location": b"fixture",
                    "name": f"Fixture House {index}",
                },
            )
            connection.execute(
                text("""
                    INSERT INTO reservations
                    (id, workspace_id, created_at, updated_at, version, property_id, provider, external_id,
                     status, checkin_at, checkout_at, observed_at, timestamp_provenance, payload_digest)
                    VALUES (:id, :workspace, :now, :now, 1, :property, 'hospitable', :booking,
                            'active', :checkin, :checkout, :now, 'source', :digest)
                """),
                {
                    "id": uuid4(),
                    "workspace": workspace,
                    "now": now,
                    "property": prop,
                    "booking": booking,
                    "checkin": now - timedelta(hours=1),
                    "checkout": now + timedelta(days=1),
                    "digest": "a" * 64,
                },
            )
    body = {
        "external_request_id": request,
        "property_external_id": property_source,
        "reservation_external_id": booking,
        "release_at": (now + timedelta(hours=1)).isoformat(),
        "deadline_at": (now + timedelta(hours=3)).isoformat(),
        "scope": "Replace the bedding",
        "duration_minutes": 30,
        "guest_access_confirmed": True,
    }
    root = "/v1/cleaner-returns"
    read_path = f"{root}/{request}"
    first = session_factory(settings, workspace_id=workspace_a)
    second = session_factory(settings, workspace_id=workspace_b)
    read_settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)
    for index, factory in enumerate((first, second)):
        with factory() as session:
            stays = json.loads(operations_stays(None, session, read_settings, SimpleNamespace(now=lambda: now)).body)
            assert [stay["property_name"] for stay in stays["stays"]] == [f"Fixture House {index}"]
    touchup_settings = SimpleNamespace(
        workspace_signup_enabled=True,
        operations_login_enabled=True,
        operations_touchup_enabled=True,
        local_scheduling_enabled=True,
    )
    with first() as session:
        task_a = json.loads(
            operations_cleaning_touchup(
                OperationsTouchupWrite(**body),
                None,
                session,
                touchup_settings,
                FrozenClock(now),
            ).body
        )
        assert guest_recovery.handle(session, "POST", root, body) == task_a
    with second.begin() as session:
        with pytest.raises(LookupError):
            guest_recovery.handle(session, "GET", read_path, None)
        with pytest.raises(AppError) as missing:
            operations_cleaning_status(UUID(request), property_source, booking, None, session, touchup_settings)
        assert missing.value.status_code == 404
        task_b = guest_recovery.handle(session, "POST", root, body)
    assert task_a["task_id"] != task_b["task_id"]
    with first.begin() as session:
        assert guest_recovery.handle(session, "GET", read_path, None) == task_a
        result = operations_cleaning_status(UUID(request), property_source, booking, None, session, touchup_settings)
        assert json.loads(result.body) == task_a
    with second.begin() as session:
        assert guest_recovery.handle(session, "GET", read_path, None) == task_b
        with pytest.raises(AppError):
            operations_cleaning_status(
                UUID(request), property_source, "unrelated-stay", None, session, touchup_settings
            )

    legacy_request = uuid4()
    with migrator.begin() as connection:
        connection.execute(
            text("UPDATE cleaning_tasks SET external_return_request_id = :request WHERE id = :task"),
            {"request": legacy_request, "task": task_a["task_id"]},
        )
    with first.begin() as session:
        result = operations_cleaning_status(legacy_request, property_source, booking, None, session, touchup_settings)
        assert json.loads(result.body) == task_a
    with second.begin() as session, pytest.raises(AppError) as denied:
        operations_cleaning_status(legacy_request, property_source, booking, None, session, touchup_settings)
    assert denied.value.status_code == 404


def test_cleaning_handoff_isolation():
    check_cleaning_handoff_isolation(get_settings(), get_engine(os.environ["MIGRATION_DATABASE_URL"]))


def test_touchup_lifecycle_shares_schedule_email_acceptance_and_completion(
    client, session, settings, clock, monkeypatch
):
    from unittest.mock import Mock

    from app.models.tables import Assignment, Cleaner, EmailDelivery, Property, Reservation
    from app.services import cleaner_coordination, email_automation
    from app.services.field_cipher import protect_text
    from app.workspaces import LEGACY_WORKSPACE_ID
    from sqlalchemy import select
    from tests.test_scheduling import _approve, _available, _cleaner, _preview, _publish, _sync

    _approve(client)
    _sync(client, [], "handoff-fixture-sync")
    starts = clock.now() + timedelta(hours=1)
    ends = starts + timedelta(hours=3)
    day = starts.date()
    home = Property(
        id=uuid4(),
        created_at=clock.now(),
        updated_at=clock.now(),
        provider="hospitable",
        external_id="fixture-handoff-house",
        name="Fixture handoff house",
        timezone="America/New_York",
        bedrooms=2,
        location_ciphertext=protect_text(
            json.dumps({"display_address": "100 Fixture Lane"}), settings.field_encryption_secret
        ),
        geocode_status="synthetic",
        active=True,
    )
    session.add(home)
    session.flush()
    stay = Reservation(
        id=uuid4(),
        created_at=clock.now(),
        updated_at=clock.now(),
        provider="hospitable",
        property_id=home.id,
        external_id="fixture-handoff-booking",
        status="active",
        checkin_at=clock.now() - timedelta(days=1),
        checkout_at=clock.now() + timedelta(days=1),
        observed_at=clock.now(),
        timestamp_provenance="source",
        payload_digest="a" * 64,
    )
    session.add(stay)
    session.commit()
    cleaner_id = UUID(
        _cleaner(
            client, name="Fixture cleaner", phone="+15555550116", properties=[home.external_id], key="handoff-cleaner"
        )
    )
    _available(client, str(cleaner_id), day.isoformat(), "synthetic:fixture-origin", key="handoff-availability")
    cleaner = session.get(Cleaner, cleaner_id)
    assert cleaner is not None
    cleaner.email_ciphertext = protect_text("cleaner@example.test", settings.field_encryption_secret)
    token = cleaner_coordination.issue_portal_link(session, settings, clock, cleaner_id)
    session.commit()

    shared = settings.model_copy(
        update={
            "workspace_signup_enabled": True,
            "operations_login_enabled": True,
            "operations_touchup_enabled": True,
            "local_scheduling_enabled": True,
        }
    )
    request_id = uuid4()
    touchup = OperationsTouchupWrite(
        external_request_id=request_id,
        property_external_id=home.external_id,
        reservation_external_id=stay.external_id,
        release_at=starts,
        deadline_at=ends,
        scope="Replace bedding",
        duration_minutes=30,
        guest_access_confirmed=True,
    )
    created = operations_cleaning_touchup(touchup, None, session, shared, clock)
    task_id = json.loads(created.body)["task_id"]

    def current_status():
        return json.loads(
            operations_cleaning_status(request_id, home.external_id, stay.external_id, None, session, shared).body
        )

    assert current_status()["status"] == "needs_manager"
    preview = _preview(client, day.isoformat(), "handoff-preview")
    assert current_status()["status"] == "needs_manager"
    session.commit()
    assert _publish(client, preview, "handoff-publish")["uncovered"] == []
    assignment = session.scalar(select(Assignment).where(Assignment.task_id == UUID(task_id)))
    assert assignment is not None
    status = current_status()
    assert status["status"] == "scheduled"
    assert status["assignment"]["cleaner_name"] == "Fixture cleaner"
    assert status["schedule_email_status"] is None

    assert email_automation._finalize_planning(session, clock, day, None)
    assert email_automation._queue(session, settings, clock, day) == 1
    session.flush()
    assert current_status()["schedule_email_status"] == "queued"
    session.commit()

    smtp = Mock()
    send = Mock()
    monkeypatch.setattr(email_automation, "connect", lambda _path: (smtp, "sender@example.test"))
    monkeypatch.setattr(email_automation, "send_connected_email", send)
    factory = session_factory(settings, workspace_id=LEGACY_WORKSPACE_ID)
    assert email_automation._drain(factory, settings, clock)["accepted"] == 1
    send.assert_called_once()
    assert send.call_args.args[3] == "cleaner@example.test"
    assert session.scalar(select(EmailDelivery.status)) == "accepted"
    status = current_status()
    assert status["schedule_email_status"] == "accepted"
    assert status["status"] == "scheduled"

    # Supply a synthetic routing baseline marker. Acceptance still checks the
    # published assignment and route using the same fixture matrix as preview.
    monkeypatch.setattr(cleaner_coordination, "current_local_baseline", lambda *_: object())
    worker = cleaner_coordination.authenticate_portal(session, settings, clock, token)
    cleaner_coordination.assignment_action(
        session, settings, clock, worker, "accept", assignment.id, assignment.version
    )
    session.commit()
    assert current_status()["status"] == "accepted"
    cleaner_coordination.assignment_action(
        session, settings, FrozenClock(assignment.planned_end), worker, "complete", assignment.id, assignment.version
    )
    session.commit()
    assert current_status()["status"] == "completed"


def test_shared_stay_change_applies_policy_without_replacing_imported_times(client, session, settings, clock):
    from app.api.routes.ingestion import OperationsStayChangeWrite, operations_stay_change
    from app.models.tables import CleaningTask, Property, Reservation, ReservationSyncRun
    from app.services.task_generation import recompute_property_tasks
    from sqlalchemy import select
    from tests.test_scheduling import _approve, _sync

    _approve(client)
    _sync(client, [], "shared-timing-fixture")
    run = session.scalar(select(ReservationSyncRun))
    assert run is not None
    run.provider = "hospitable"
    home = Property(
        id=uuid4(),
        created_at=clock.now(),
        updated_at=clock.now(),
        provider="hospitable",
        external_id="fixture-stay-home",
        name="Fixture stay house",
        timezone="America/New_York",
        bedrooms=2,
        location_ciphertext=b"fixture",
        geocode_status="synthetic",
        active=True,
    )
    session.add(home)
    session.flush()
    original = clock.now() + timedelta(hours=1)
    incoming = original + timedelta(hours=7)
    stays = []
    for name, arrival, departure in (
        ("current", clock.now() - timedelta(days=1), original),
        ("upcoming", incoming, incoming + timedelta(days=2)),
    ):
        booking = Reservation(
            id=uuid4(),
            created_at=clock.now(),
            updated_at=clock.now(),
            provider="hospitable",
            property_id=home.id,
            external_id=name,
            status="active",
            checkin_at=arrival,
            checkout_at=departure,
            observed_at=clock.now(),
            timestamp_provenance="source",
            payload_digest="a" * 64,
        )
        session.add(booking)
        stays.append(booking)
    session.flush()
    recompute_property_tasks(session, clock=clock, property_id=home.id)
    session.commit()

    stay = stays[0]
    shared = settings.model_copy(
        update={
            "workspace_signup_enabled": True,
            "operations_login_enabled": True,
            "operations_touchup_enabled": True,
            "local_scheduling_enabled": True,
        }
    )
    request = OperationsStayChangeWrite(
        request_id=uuid4(),
        property_external_id=home.external_id,
        reservation_external_id=stay.external_id,
        kind="late_checkout",
        target_at=original + timedelta(hours=1),
        booked_check_in=stay.checkin_at,
        booked_check_out=original,
    )
    changed_booking = request.model_copy(update={"booked_check_out": original - timedelta(minutes=15)})
    with pytest.raises(AppError) as stale:
        operations_stay_change(changed_booking, None, session, shared, clock)
    assert stale.value.status_code == 422
    assert stay.approved_guest_checkout_at is None
    decision = operations_stay_change(request, None, session, shared, clock)
    assert json.loads(decision.body)["decision"] == "approved"
    assert stay.checkout_at == original
    assert stay.approved_guest_checkout_at == request.target_at
    task = session.scalar(select(CleaningTask).where(CleaningTask.source_reservation_id == stay.id))
    assert task is not None and task.release_at == request.target_at
