from __future__ import annotations

import os
from datetime import UTC, datetime

from app.adapters.reservations.mock import MockFeed, set_mock_feed
from app.clock import SystemClock
from app.config import get_settings
from app.db import get_engine, session_factory
from app.domain.tasks import eligible_for_future_dispatch
from app.models import CleaningTask, Reservation
from app.services.task_generation import recompute_property_tasks
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from tests.support import auth_headers, count_rows, valid_settings

MANAGER = "synthetic-manager"


def _headers(subject: str, key: str, roles: tuple[str, ...] = ("manager",)) -> dict[str, str]:
    return auth_headers(subject, key=key, roles=roles)


def _approve_scenario_policy(client: TestClient, *, buffer: int = 30, bedrooms: dict[str, int] | None = None) -> None:
    settings = valid_settings()
    settings["readiness_buffer_minutes"] = buffer
    if bedrooms is not None:
        settings["duration_minutes_by_bedrooms"] = bedrooms
    created = client.post(
        "/v1/policy-versions",
        json={"settings": settings, "reason": "Scenario fixture. Not a production approval."},
        headers=_headers("synthetic-owner", "policy-create", ("owner",)),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/v1/policy-versions/{created.json()['id']}/approve",
        json={"approval_note": "Scenario fixture only.", "effective_at": "2026-11-01T00:00:00Z"},
        headers={**_headers("synthetic-owner", "policy-approve", ("owner",)), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text


def _sync(client: TestClient, key: str) -> None:
    response = client.post(
        "/v1/reservation-syncs",
        json={"provider": "mock", "mode": "full"},
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 202, response.text


def _property_id(external_id: str) -> str:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        value = connection.execute(
            text("SELECT id FROM properties WHERE external_id = :external_id"),
            {"external_id": external_id},
        ).scalar_one()
    return str(value)


def test_without_approved_policy_caps_deadlines_but_blocks_missing_configuration(client: TestClient) -> None:
    set_mock_feed(
        MockFeed(
            pages=[
                [
                    {
                        "provider": "mock",
                        "external_id": "R-A",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-09T16:00:00-05:00",
                        "checkout_at": "2026-11-10T10:00:00-05:00",
                        "source_revision": "1",
                    },
                    {
                        "provider": "mock",
                        "external_id": "R-B",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-10T16:00:00-05:00",
                        "checkout_at": "2026-11-12T10:00:00-05:00",
                        "source_revision": "1",
                    },
                ]
            ]
        )
    )
    _sync(client, "no-policy")
    listed = client.get("/v1/tasks", headers=_headers(MANAGER, "list"))
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    assert all(item["deadline_at"] is not None for item in items)
    assert all(item["status"] == "ready" for item in items), "Both tasks should have status 'ready'"
    non_dispatchable = [item for item in items if item["dispatchable"] is False]
    assert len(non_dispatchable) == 1, "Exactly one task should be non-dispatchable"
    blocked_task = non_dispatchable[0]
    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        exception_query = text("SELECT e.code FROM exceptions e WHERE e.task_id = :task_id AND e.status = 'open'")
        exception_code = connection.execute(exception_query, {"task_id": blocked_task["id"]}).scalar_one()
        reservation_query = text(
            "SELECT r.external_id FROM reservations r "
            "JOIN cleaning_tasks t ON t.source_reservation_id = r.id WHERE t.id = :task_id"
        )
        blocked_reservation = connection.execute(reservation_query, {"task_id": blocked_task["id"]}).scalar_one()
    assert exception_code == "MISSING_READINESS_BUFFER", f"Expected MISSING_READINESS_BUFFER, got {exception_code}"
    assert blocked_reservation == "R-A", "R-A's task should be blocked by MISSING_READINESS_BUFFER"


def test_at01_deadline_uses_supplied_buffer_not_arrival_time(client: TestClient) -> None:
    _approve_scenario_policy(client)
    set_mock_feed(
        MockFeed(
            pages=[
                [
                    {
                        "provider": "mock",
                        "external_id": "R-A",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-09T16:00:00-05:00",
                        "checkout_at": "2026-11-10T10:00:00-05:00",
                        "source_revision": "1",
                    },
                    {
                        "provider": "mock",
                        "external_id": "R-B",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-10T16:00:00-05:00",
                        "checkout_at": "2026-11-12T10:00:00-05:00",
                        "source_revision": "1",
                    },
                ]
            ]
        )
    )
    _sync(client, "at01")
    _sync(client, "at01-replay")
    rows = client.get("/v1/tasks", headers=_headers(MANAGER, "tasks")).json()["items"]
    by_release = {row["release_at"]: row for row in rows}
    predecessor = by_release["2026-11-10T15:00:00+00:00"]
    successor = by_release["2026-11-12T15:00:00+00:00"]
    assert predecessor["deadline_at"] == "2026-11-10T20:30:00+00:00"
    assert predecessor["deadline_at"] != "2026-11-10T21:00:00+00:00"
    assert predecessor["status"] == "ready"
    assert predecessor["duration_minutes"] == 120
    assert successor["deadline_at"] == "2026-11-14T15:00:00+00:00"
    assert successor["status"] == "ready"
    assert successor["dispatchable"] is True
    assert count_rows("cleaning_tasks") == 2
    events_after_first = count_rows("outbox_events")
    _sync(client, "at01-replay-2")
    assert count_rows("cleaning_tasks") == 2
    assert count_rows("outbox_events") == events_after_first


def test_extension_keeps_task_identity_and_updates_release(client: TestClient) -> None:
    _approve_scenario_policy(client)
    base = {
        "provider": "mock",
        "external_id": "R-A",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-09T16:00:00-05:00",
        "checkout_at": "2026-11-10T10:00:00-05:00",
        "source_revision": "1",
    }
    nxt = {
        "provider": "mock",
        "external_id": "R-B",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-10T16:00:00-05:00",
        "checkout_at": "2026-11-12T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[base, nxt]]))
    _sync(client, "ext-1")
    first_id = client.get("/v1/tasks?status=ready", headers=_headers(MANAGER, "ready")).json()["items"][0]["id"]
    extended = dict(base)
    extended["checkout_at"] = "2026-11-11T10:00:00-05:00"
    extended["source_revision"] = "2"
    moved = dict(nxt)
    moved["checkin_at"] = "2026-11-11T16:00:00-05:00"
    moved["source_revision"] = "2"
    set_mock_feed(MockFeed(pages=[[extended, moved]]))
    _sync(client, "ext-2")
    ready = client.get("/v1/tasks?status=ready", headers=_headers(MANAGER, "ready-2")).json()["items"]
    ready = [item for item in ready if item["id"] == first_id]
    assert len(ready) == 1
    assert ready[0]["id"] == first_id
    assert ready[0]["release_at"] == "2026-11-11T15:00:00+00:00"
    assert ready[0]["deadline_at"] == "2026-11-11T20:30:00+00:00"


def test_source_cancel_preserves_completed_history(client: TestClient) -> None:
    _approve_scenario_policy(client)
    rows = [
        {
            "provider": "mock",
            "external_id": "R-OLD",
            "property_external_id": "P-B",
            "status": "active",
            "checkin_at": "2026-10-01T16:00:00-04:00",
            "checkout_at": "2026-10-02T10:00:00-04:00",
            "source_revision": "1",
        },
        {
            "provider": "mock",
            "external_id": "R-A",
            "property_external_id": "P-B",
            "status": "active",
            "checkin_at": "2026-11-09T16:00:00-05:00",
            "checkout_at": "2026-11-10T10:00:00-05:00",
            "source_revision": "1",
        },
    ]
    set_mock_feed(MockFeed(pages=[rows]))
    _sync(client, "hist-1")
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as connection:
        connection.execute(
            text(
                "UPDATE cleaning_tasks SET status='completed', completed_at=now(), "
                "completion_actor='synthetic-manager' "
                "WHERE source_reservation_id = (SELECT id FROM reservations WHERE external_id='R-OLD')"
            )
        )
    canceled = dict(rows[1])
    canceled["status"] = "canceled"
    canceled["source_revision"] = "2"
    set_mock_feed(MockFeed(pages=[[rows[0], canceled]]))
    _sync(client, "hist-2")
    listed = client.get("/v1/tasks", headers=_headers(MANAGER, "hist-list")).json()["items"]
    statuses = {item["status"] for item in listed}
    assert "completed" in statuses
    assert "canceled" in statuses
    completed = next(item for item in listed if item["status"] == "completed")
    assert completed["completion_actor"] == "synthetic-manager"


def test_next_arrival_cancellation_restores_48h_deadline_cap(client: TestClient) -> None:
    _approve_scenario_policy(client)
    first = {
        "provider": "mock",
        "external_id": "R-A",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-09T16:00:00-05:00",
        "checkout_at": "2026-11-10T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[first]]))
    _sync(client, "pred-1")
    pending = client.get("/v1/tasks?status=ready", headers=_headers(MANAGER, "pred-list")).json()["items"]
    assert len(pending) == 1
    assert pending[0]["deadline_at"] == "2026-11-12T15:00:00+00:00"
    task_id = pending[0]["id"]
    version = pending[0]["version"]
    targeted = client.post(
        f"/v1/tasks/{task_id}/target",
        json={"manager_target_at": "2026-11-10T18:00:00-05:00", "reason": "Explicit synthetic target."},
        headers={**_headers(MANAGER, "target"), "If-Match": f'"{version}"'},
    )
    assert targeted.status_code == 200, targeted.text
    assert targeted.json()["manager_target_at"] == "2026-11-10T23:00:00+00:00"
    assert targeted.json()["deadline_at"] == "2026-11-12T15:00:00+00:00"
    assert targeted.json()["status"] == "ready"
    arrival = {
        "provider": "mock",
        "external_id": "R-B",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-10T16:00:00-05:00",
        "checkout_at": "2026-11-12T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[first, arrival]]))
    _sync(client, "pred-2")
    ready = client.get("/v1/tasks?status=ready", headers=_headers(MANAGER, "pred-ready")).json()["items"]
    predecessor = next(item for item in ready if item["id"] == task_id)
    assert predecessor["deadline_at"] == "2026-11-10T20:30:00+00:00"
    assert predecessor["status"] != "canceled"
    blocked = client.post(
        f"/v1/tasks/{task_id}/target",
        json={"manager_target_at": "2026-11-10T19:00:00-05:00", "reason": "Too late."},
        headers={**_headers(MANAGER, "target-2"), "If-Match": f'"{predecessor["version"]}"'},
    )
    assert blocked.status_code == 409
    canceled_arrival = dict(arrival)
    canceled_arrival["status"] = "canceled"
    canceled_arrival["source_revision"] = "2"
    set_mock_feed(MockFeed(pages=[[first, canceled_arrival]]))
    _sync(client, "pred-3")
    after = client.get("/v1/tasks", headers=_headers(MANAGER, "pred-after")).json()["items"]
    predecessor = next(item for item in after if item["id"] == task_id)
    assert predecessor["deadline_at"] == "2026-11-12T15:00:00+00:00"
    assert predecessor["status"] == "ready"
    assert predecessor["manager_target_at"] == "2026-11-10T23:00:00+00:00"


def test_duration_override_then_bedroom_default_and_five_bedroom_uses_formula(client: TestClient) -> None:
    settings = valid_settings()
    settings["duration_minutes_by_bedrooms"] = {"0": 60, "1": 90, "2": 120, "3": 150, "4": 180}
    _approve_scenario_policy(client, bedrooms=settings["duration_minutes_by_bedrooms"])  # type: ignore[arg-type]
    property_a = _property_id("P-A")
    property_b = _property_id("P-B")
    patched = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": 150},
        headers={**_headers(MANAGER, "dur-1"), "If-Match": '"1"'},
    )
    assert patched.status_code == 200, patched.text
    stay = {
        "provider": "mock",
        "external_id": "R-A",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-01T16:00:00-04:00",
        "checkout_at": "2026-11-02T10:00:00-05:00",
        "source_revision": "1",
    }
    five = dict(stay)
    five["external_id"] = "R-FIVE"
    five["property_external_id"] = "P-B"
    set_mock_feed(MockFeed(pages=[[stay, five]]))
    client.patch(
        f"/v1/properties/{property_b}",
        json={"bedrooms": 5, "clear_duration_override": True},
        headers={**_headers(MANAGER, "dur-b"), "If-Match": '"1"'},
    )
    _sync(client, "dur-sync")
    rows = client.get("/v1/tasks", headers=_headers(MANAGER, "dur-list")).json()["items"]
    by_property = {row["property_id"]: row for row in rows}
    assert by_property[property_a]["duration_minutes"] == 150
    assert by_property[property_a]["duration_source"] == "property_override"
    cleared = client.patch(
        f"/v1/properties/{property_a}",
        json={"clear_duration_override": True},
        headers={**_headers(MANAGER, "dur-clear"), "If-Match": f'"{patched.json()["version"]}"'},
    )
    assert cleared.status_code == 200, cleared.text
    rows = client.get("/v1/tasks", headers=_headers(MANAGER, "dur-list-2")).json()["items"]
    by_property = {row["property_id"]: row for row in rows}
    assert by_property[property_a]["duration_minutes"] == 120
    assert by_property[property_a]["duration_source"] == "bedroom_default"
    assert by_property[property_b]["duration_minutes"] == 210
    assert by_property[property_b]["duration_source"] == "bedroom_default"


def test_fr05_rejects_invalid_duration_overrides(client: TestClient) -> None:
    """FR-05: invalid overrides are rejected (zero, negative, non-integer, or malformed values)."""
    _approve_scenario_policy(client)
    property_a = _property_id("P-A")
    zero = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": 0},
        headers={**_headers(MANAGER, "dur-zero"), "If-Match": '"1"'},
    )
    assert zero.status_code == 422, "Zero duration override should be rejected"
    negative = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": -30},
        headers={**_headers(MANAGER, "dur-neg"), "If-Match": '"1"'},
    )
    assert negative.status_code == 422, "Negative duration override should be rejected"
    fractional = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": 1.5},
        headers={**_headers(MANAGER, "dur-frac"), "If-Match": '"1"'},
    )
    assert fractional.status_code == 422, "Fractional duration override (1.5) should be rejected"
    string_val = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": "abc"},
        headers={**_headers(MANAGER, "dur-str"), "If-Match": '"1"'},
    )
    assert string_val.status_code == 422, "String duration override ('abc') should be rejected"
    valid = client.patch(
        f"/v1/properties/{property_a}",
        json={"duration_override_minutes": 90},
        headers={**_headers(MANAGER, "dur-valid"), "If-Match": '"1"'},
    )
    assert valid.status_code == 200, "Positive duration override should be accepted"
    assert valid.json()["duration_override_minutes"] == 90


def test_recompute_rolls_back_with_the_reservation(client: TestClient) -> None:
    _approve_scenario_policy(client)
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    property_id = _property_id("P-A")
    with factory() as session:
        session.add(
            Reservation(
                id=__import__("uuid").uuid4(),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                version=1,
                property_id=__import__("uuid").UUID(property_id),
                provider="mock",
                external_id="R-ROLLBACK",
                status="active",
                checkin_at=datetime(2026, 11, 9, 21, tzinfo=UTC),
                checkout_at=datetime(2026, 11, 10, 15, tzinfo=UTC),
                source_revision="1",
                source_updated_at=None,
                observed_at=datetime.now(UTC),
                timestamp_provenance="test",
                payload_digest="rollback",
                last_sync_run_id=None,
            )
        )
        session.flush()
        recompute_property_tasks(session, clock=SystemClock(), property_id=__import__("uuid").UUID(property_id))
        assert session.scalar(select(CleaningTask.id)) is not None
        session.rollback()
    assert count_rows("cleaning_tasks") == 0


def test_pending_task_is_not_dispatchable() -> None:
    assert (
        eligible_for_future_dispatch(status="pending_deadline", duration_minutes=120, blocking_exception_open=False)
        is False
    )
    assert eligible_for_future_dispatch(status="ready", duration_minutes=None, blocking_exception_open=False) is False
    assert eligible_for_future_dispatch(status="ready", duration_minutes=120, blocking_exception_open=True) is False
