from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from app.api.app_factory import create_app
from app.clock import FrozenClock
from app.config import get_settings
from app.demo.server import DEMO_NOW, build_demo_app
from app.models import Assignment, OperationalException, Property, ScheduleRun, SmsMessage
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.completed_fixture import db
from tests.support import auth_headers
from tests.test_demo import demo_settings
from tests.test_scheduling import _approve, _available, _cleaner, _sync, _turnover
from tests.test_sms import _opt_in


def headers(key: str = "setup", viewer: bool = False) -> dict[str, str]:
    return auth_headers(
        "synthetic-viewer" if viewer else "synthetic-manager", key=key, roles=("viewer",) if viewer else ("manager",)
    )


def cleaner(client: TestClient) -> str:
    return _cleaner(
        client, name="Synthetic setup cleaner", phone="+15555550919", properties=["P-A"], key="setup-cleaner"
    )


def test_setup_readiness_is_not_a_live_enable_switch(client: TestClient) -> None:
    assert client.get("/v1/setup").status_code == 401
    body = client.get("/v1/setup", headers=headers(viewer=True)).json()
    assert body["mode"] == "mock"
    assert body["live_automation_ready"] is False
    assert body["cleaners"] == 0
    assert body["policy_approved"] is False
    assert body["source_fresh"] is False
    assert "phone" not in str(body)
    row = cleaner(client)
    body = client.get("/v1/setup?service_date=2026-11-10", headers=headers()).json()
    assert body["reservation_window"]["from_time"] == "2026-11-10T05:00:00+00:00"
    assert body["cleaners_without_workday"] == 1
    _available(client, row, "2026-11-10", "synthetic:setup", key="setup-day")
    body = client.get("/v1/setup?service_date=2026-11-10", headers=headers()).json()
    assert body["cleaners_without_workday"] == 0
    assert body["cleaners_without_consent"] == 1
    assert body["live_automation_ready"] is False


def test_default_dates_come_from_business_clock(client: TestClient) -> None:
    app = create_app(get_settings(), FrozenClock(datetime(2026, 11, 11, 1, tzinfo=UTC)))
    with TestClient(app) as fixed:
        assert fixed.get("/v1/setup", headers=headers()).json()["business_date"] == "2026-11-10"
        assert fixed.get("/v1/dashboard", headers=headers()).json()["service_date"] == "2026-11-10"


def test_setup_reports_cleaning_time_defaults_for_all_bedroom_counts(client: TestClient) -> None:
    with db() as session:
        properties = session.scalars(select(Property)).all()
        for prop in properties:
            prop.bedrooms = 2
            prop.duration_override_minutes = None
        property_id = properties[0].id
        session.commit()
    before = client.get("/v1/setup", headers=headers()).json()
    assert before["properties_needing_cleaning_time"] == 0
    assert before["cleaning_minutes_by_bedrooms"]["2"] == 120
    _approve(client)
    assert client.get("/v1/setup", headers=headers()).json()["properties_needing_cleaning_time"] == 0
    with db() as session:
        prop = session.get(Property, property_id)
        prop.bedrooms = 5
        session.commit()
    setup = client.get("/v1/setup", headers=headers()).json()
    assert setup["properties_needing_cleaning_time"] == 0
    assert setup["cleaning_minutes_by_bedrooms"]["5"] == 210
    with db() as session:
        prop = session.get(Property, property_id)
        prop.duration_override_minutes = 180
        session.commit()
    assert client.get("/v1/setup", headers=headers()).json()["properties_needing_cleaning_time"] == 0


def test_workday_converts_eastern_time_and_rejects_stale_writes(client: TestClient) -> None:
    row = cleaner(client)
    path = f"/v1/cleaners/{row}/workday"
    body = {
        "service_date": "2026-07-10",
        "start_time": "09:00",
        "end_time": "17:00",
        "break_start": "12:00",
        "break_end": "12:30",
        "origin_label": "synthetic:setup",
        "expected_version": 0,
    }
    assert client.post(path, json=body, headers=headers(viewer=True)).status_code == 403
    first = client.post(path, json=body, headers=headers("day-create"))
    assert first.status_code == 200, first.text
    assert client.post(path, json=body, headers=headers("day-create")).json() == first.json()
    result = client.get(f"/v1/cleaners/{row}/availability?service_date=2026-07-10", headers=headers()).json()
    assert result["work_intervals"][0]["start_at"].startswith("2026-07-10T13:00")
    assert result["unavailable_intervals"][0]["start_at"].startswith("2026-07-10T16:00")
    assert (
        client.get(f"/v1/cleaners/{row}/availability?service_date=2026-07-10", headers=headers(viewer=True)).status_code
        == 403
    )
    assert client.post(path, json=body, headers=headers("stale-day")).status_code == 409
    updated = client.post(
        path, json={**body, "expected_version": 1, "end_time": "16:00"}, headers=headers("day-update")
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2


def test_workday_rejects_dst_gaps_ambiguity_and_invalid_breaks(client: TestClient) -> None:
    row = cleaner(client)
    path = f"/v1/cleaners/{row}/workday"
    base = {
        "service_date": "2026-11-10",
        "start_time": "09:00",
        "end_time": "17:00",
        "origin_label": "synthetic:setup",
        "expected_version": 0,
    }
    for changes in [
        {"service_date": "2026-03-08", "start_time": "02:30"},
        {"service_date": "2026-11-01", "start_time": "01:30"},
        {"end_time": "08:00"},
        {"break_start": "12:00"},
        {"break_start": "08:00", "break_end": "10:00"},
    ]:
        response = client.post(path, json={**base, **changes}, headers=headers(str(uuid.uuid4())))
        assert response.status_code == 422, response.text


def demo_app(monkeypatch):
    monkeypatch.setenv("LOCAL_DEMO", "1")
    settings = get_settings()
    monkeypatch.setattr(
        "app.demo.server.create_app", lambda _settings, _clock: create_app(settings, FrozenClock(DEMO_NOW))
    )
    return build_demo_app(demo_settings())


def wait_cycle(client: TestClient, previous: str | None = None) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get("/v1/demo/account", headers=headers())
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        result = response.json()
        assert result["last_error"] is None, result
        if result["last_run_at"] and result["last_run_at"] != previous and not result["busy"]:
            return result
        time.sleep(0.05)
    raise AssertionError("Demo cycle did not finish")


def test_demo_automation_publishes_once_and_repeated_cycles_preserve_versions(client: TestClient, monkeypatch) -> None:
    _approve(client)
    _sync(client, _turnover("demo-setup", "P-A", "2026-11-11"), "setup-sync")
    row = cleaner(client)
    _available(client, row, "2026-11-11", "synthetic:setup", key="setup-day")
    _opt_in(client, row)
    app = demo_app(monkeypatch)
    with TestClient(app) as demo:
        assert demo.get("/v1/demo/account", headers=headers(viewer=True)).json()["connection"] == "simulated"
        assert demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers(viewer=True)).status_code == 403
        assert demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers()).status_code == 200
        first = wait_cycle(demo)
        with db() as session:
            original = [(a.id, a.version, a.cleaner_schedule_version_id) for a in session.scalars(select(Assignment))]
            messages = session.scalar(select(func.count()).select_from(SmsMessage))
        assert original
        assert messages == 0  # Fixed demo clock is 1 PM Eastern, before evening dispatch.
        assert demo.post("/v1/demo/evening", headers=headers(viewer=True)).status_code == 403
        delivered = demo.post("/v1/demo/evening", headers=headers())
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["dispatch"]["recorded"] == 1
        assert demo.post("/v1/demo/evening", headers=headers()).json()["dispatch"]["recorded"] == 0
        with db() as session:
            messages = session.scalar(select(func.count()).select_from(SmsMessage))
        demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers())
        second = wait_cycle(demo, str(first["last_run_at"]))
        assert second["published_run_id"] == first["published_run_id"]
        with db() as session:
            assert [
                (a.id, a.version, a.cleaner_schedule_version_id) for a in session.scalars(select(Assignment))
            ] == original
            assert session.scalar(select(func.count()).select_from(SmsMessage)) == messages
            assert session.scalar(select(func.count()).select_from(ScheduleRun)) == 1
        assert demo.post("/v1/demo/automation", json={"enabled": False}, headers=headers()).json()["enabled"] is False
        reply = demo.post(
            "/v1/demo/reply",
            json={"assignment_id": str(original[0][0]), "command": "CONFIRM", "event_id": str(uuid.uuid4())},
            headers=headers(),
        )
        assert reply.status_code == 200, reply.text
        with db() as session:
            acknowledged = [
                (a.id, a.version, a.cleaner_schedule_version_id, a.acknowledged_at)
                for a in session.scalars(select(Assignment))
            ]
        assert acknowledged[0][-1] is not None
    with TestClient(demo_app(monkeypatch)) as restarted:
        assert restarted.get("/v1/demo/account", headers=headers()).json()["enabled"] is False
        restarted.post("/v1/demo/automation", json={"enabled": True}, headers=headers())
        wait_cycle(restarted)
        with db() as session:
            assert [
                (a.id, a.version, a.cleaner_schedule_version_id, a.acknowledged_at)
                for a in session.scalars(select(Assignment))
            ] == acknowledged
        restarted.post("/v1/demo/automation", json={"enabled": False}, headers=headers())


def test_demo_failure_pauses_and_standard_app_does_not_mount_proxy(client: TestClient, monkeypatch) -> None:
    for path in ("/v1/demo/account", "/v1/demo/automation", "/v1/demo/turnovers"):
        assert client.get(path, headers=headers()).status_code == 404
    with TestClient(demo_app(monkeypatch)) as demo:
        demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = demo.get("/v1/demo/account", headers=headers()).json()
            if result["last_error"]:
                assert result["enabled"] is False
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Failure did not pause automation")


def test_proxy_turnover_is_idempotent_and_creates_cleaning_work(client: TestClient, monkeypatch) -> None:
    _approve(client)
    prop = client.get("/v1/properties", headers=headers()).json()["items"][0]
    with TestClient(demo_app(monkeypatch)) as demo:
        body = {
            "property_id": prop["id"],
            "checkout_date": "2026-11-11",
            "checkout_time": "10:00",
            "next_checkin_time": "16:00",
        }
        assert demo.post("/v1/demo/turnovers", json=body, headers=headers(viewer=True)).status_code == 403
        result = demo.post("/v1/demo/turnovers", json=body, headers=headers("proxy-stay"))
        assert result.status_code == 201, result.text
        assert demo.post("/v1/demo/turnovers", json=body, headers=headers("proxy-stay")).json() == result.json()
        tasks = demo.get("/v1/tasks", headers=headers()).json()["items"]
        assert len(tasks) == 2
        assert sum(task["status"] == "ready" for task in tasks) == 2


def test_property_cursor_keeps_new_houses_selectable_beyond_first_page(client: TestClient) -> None:
    created = client.post(
        "/v1/properties",
        json={
            "name": "Synthetic extra house",
            "external_id": "synthetic-extra",
            "provider": "mock",
            "bedrooms": 2,
            "duration_override_minutes": 90,
            "location": {"latitude": 35.2, "longitude": -80.8},
            "active": True,
        },
        headers=headers("extra-house"),
    )
    assert created.status_code == 201, created.text
    seen: list[str] = []
    cursor = None
    while True:
        query = "/v1/properties?limit=40" + (f"&cursor={cursor}" if cursor else "")
        response = client.get(query, headers=headers(viewer=True))
        assert response.status_code == 200
        body = response.json()
        assert all("latitude" not in row for row in body["items"])
        seen.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    assert created.json()["id"] in seen
    assert client.get(f"/v1/properties?cursor={uuid.uuid4()}", headers=headers()).status_code == 422


def _find_property_by_external_id(demo: TestClient, external_id: str) -> dict:
    """Find a property by external_id, following pagination if needed."""
    cursor = None
    while True:
        url = "/v1/properties?limit=100"
        if cursor:
            url = f"{url}&cursor={cursor}"
        response = demo.get(url, headers=headers())
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        for p in data["items"]:
            if p["external_id"] == external_id:
                return p
        cursor = data.get("next_cursor")
        if not cursor:
            break
    raise ValueError(f"Property {external_id} not found")


def test_automation_surfaces_new_uncovered_work_even_when_placed_route_is_unchanged(
    client: TestClient, monkeypatch
) -> None:
    _approve(client)
    _sync(client, _turnover("demo-uncovered", "P-A", "2026-11-11"), "uncovered-sync")
    row = cleaner(client)
    _available(client, row, "2026-11-11", "synthetic:setup", key="uncovered-day")
    app = demo_app(monkeypatch)
    with TestClient(app) as demo:
        demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers())
        first = wait_cycle(demo)
        prop = _find_property_by_external_id(demo, "P-B")
        body = {
            "property_id": prop["id"],
            "checkout_date": "2026-11-11",
            "checkout_time": "10:00",
            "next_checkin_time": "16:00",
        }
        response = demo.post("/v1/demo/turnovers", json=body, headers=headers("uncovered-stay"))
        assert response.status_code == 201, response.text
        demo.post("/v1/demo/automation", json={"enabled": True}, headers=headers())
        second = wait_cycle(demo, str(first["last_run_at"]))
        assert second["published_run_id"] != first["published_run_id"]
        with db() as session:
            uncovered = list(
                session.scalars(select(OperationalException).where(OperationalException.code == "INELIGIBLE"))
            )
            assert uncovered
        demo.post("/v1/demo/automation", json={"enabled": False}, headers=headers())
