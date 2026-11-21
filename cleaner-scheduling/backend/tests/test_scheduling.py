from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.adapters.factory import build_routing_service
from app.adapters.reservations.mock import MockFeed, set_mock_feed
from app.adapters.routing.mock import set_travel_overrides
from app.adapters.sms.mock import RecordingSmsGateway
from app.clock import FrozenClock
from app.config import get_settings
from app.db import session_factory
from app.domain.errors import AppError, ConfigError
from app.domain.scheduling import solve
from app.workers.celery_app import celery_app
from app.workers.dispatch_clock import dispatch_clock_tick
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.support import auth_headers, valid_settings

NY = ZoneInfo("America/New_York")
MANAGER = "synthetic-manager"
OWNER = "synthetic-owner"


def _headers(subject: str, key: str, roles: tuple[str, ...] = ("manager",)) -> dict[str, str]:
    return auth_headers(subject, key=key, roles=roles)


def _approve(client: TestClient) -> None:
    created = client.post(
        "/v1/policy-versions",
        json={"settings": valid_settings(), "reason": "Scenario fixture. Not a production approval."},
        headers=_headers(OWNER, "policy-create", ("owner",)),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/v1/policy-versions/{created.json()['id']}/approve",
        json={"approval_note": "Scenario fixture only.", "effective_at": "2026-11-01T00:00:00Z"},
        headers={**_headers(OWNER, "policy-approve", ("owner",)), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text


def _property_id(external_id: str) -> str:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        value = connection.execute(
            text("SELECT id FROM properties WHERE external_id = :external_id"),
            {"external_id": external_id},
        ).scalar_one()
    return str(value)


def _sync(client: TestClient, stays: list[dict[str, object]], key: str) -> None:
    clock = getattr(client.app.state, "clock", None)
    if clock is None:
        from tests.conftest import TEST_CLOCK

        clock = TEST_CLOCK
    set_mock_feed(MockFeed(pages=[stays], observed_at=clock.now()))
    response = client.post(
        "/v1/reservation-syncs",
        json={"provider": "mock", "mode": "full"},
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 202, response.text


def _stay(external: str, prop: str, checkin: str, checkout: str, revision: str = "1") -> dict[str, object]:
    return {
        "provider": "mock",
        "external_id": external,
        "property_external_id": prop,
        "status": "active",
        "checkin_at": checkin,
        "checkout_at": checkout,
        "source_revision": revision,
    }


def _turnover(external: str, prop: str, day: str, revision: str = "1") -> list[dict[str, object]]:
    return [
        _stay(external, prop, _previous(day), f"{day}T10:00:00-05:00", revision),
        _stay(f"{external}-NEXT", prop, f"{day}T16:00:00-05:00", _next_day(day), revision),
    ]


def _previous(day: str) -> str:
    instant = datetime.fromisoformat(f"{day}T12:00:00-05:00") - timedelta(days=1)
    return instant.date().isoformat() + "T16:00:00-05:00"


def _next_day(day: str) -> str:
    instant = datetime.fromisoformat(f"{day}T12:00:00-05:00") + timedelta(days=1)
    return instant.date().isoformat() + "T10:00:00-05:00"


def _cleaner(
    client: TestClient,
    *,
    name: str,
    phone: str,
    properties: list[str],
    jobs: int = 2,
    service: int = 300,
    duty: int = 420,
    key: str,
) -> str:
    response = client.post(
        "/v1/cleaners",
        json={
            "display_name": name,
            "phone": phone,
            "max_jobs": jobs,
            "max_service_minutes": service,
            "max_duty_minutes": duty,
            "eligibility_property_ids": [_property_id(item) for item in properties],
        },
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 201, response.text
    assert response.json()["phone_stored"] is True
    return str(response.json()["id"])


def _available(
    client: TestClient,
    cleaner_id: str,
    day: str,
    origin: str,
    *,
    start: str = "09:00:00",
    end: str = "17:00:00",
    breaks: list[dict[str, str]] | None = None,
    jobs: int | None = None,
    service: int | None = None,
    duty: int | None = None,
    key: str,
    expected_version: int | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/v1/cleaners/{cleaner_id}/availability",
        json={
            "service_date": day,
            "status": "available",
            "work_intervals": [{"start_at": f"{day}T{start}-05:00", "end_at": f"{day}T{end}-05:00"}],
            "unavailable_intervals": breaks or [],
            "origin_label": origin,
            "max_jobs_override": jobs,
            "max_service_override": service,
            "max_duty_override": duty,
            "expected_version": expected_version,
        },
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _preview(client: TestClient, day: str, key: str) -> dict[str, object]:
    response = client.post(
        "/v1/schedules/previews",
        json={"service_date": day},
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 200, response.text
    body = dict(response.json())
    assert body["sms_sent"] is False
    assert body["solver_status"] != "optimal" or body["solver_version"]
    return body


def _publish(client: TestClient, preview: dict[str, object], key: str, match: str = '"1"') -> dict[str, object]:
    response = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
        headers={**_headers(MANAGER, key), "If-Match": match},
    )
    assert response.status_code == 200, response.text
    body = dict(response.json())
    assert body["sms_sent"] is False
    return body


def _tasks(client: TestClient) -> list[dict[str, object]]:
    response = client.get("/v1/tasks?limit=100", headers=_headers(MANAGER, "tasks-read"))
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _rows(query: str, **params: object) -> list[tuple[object, ...]]:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        return list(connection.execute(text(query), params).all())


def test_solver_timeout_is_not_labeled_optimal() -> None:
    result = solve([], [], lambda _origin, _dest: 0, time_limit_seconds=0)
    assert result.solver_status == "feasible_not_proven_optimal"
    assert "optimal" != result.solver_status


def test_google_routing_is_not_constructed() -> None:
    with pytest.raises(ConfigError):
        build_routing_service("google")


def test_preview_requires_approved_policy_and_sends_no_sms(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("SMS send was called")

    monkeypatch.setattr(RecordingSmsGateway, "send", _forbidden)
    denied = client.post(
        "/v1/schedules/previews",
        json={"service_date": "2026-11-10"},
        headers=_headers(MANAGER, "preview-denied"),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "policy_not_approved"


def test_at03_feasible_window_publishes_without_using_arrival_as_deadline(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-at03")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550101", properties=["P-A"], key="cleaner-at03")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 1800, ("P-A", "synthetic:origin-a"): 1800})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at03")
    preview = _preview(client, "2026-11-10", "preview-at03")
    placed = list(preview["candidate_plan"]["placed"])
    assert len(placed) == 1
    start = datetime.fromisoformat(str(placed[0]["start"]))
    end = datetime.fromisoformat(str(placed[0]["end"]))
    assert start.astimezone(NY) >= datetime(2026, 11, 10, 10, 0, tzinfo=NY)
    assert end.astimezone(NY) <= datetime(2026, 11, 10, 15, 30, tzinfo=NY)
    task = next(item for item in _tasks(client) if item["property_id"] == _property_id("P-A") and item["deadline_at"])
    assert str(task["deadline_at"]).startswith("2026-11-10T20:30:00")
    published = _publish(client, preview, "publish-at03")
    assert published["uncovered"] == []
    live = _rows("SELECT status, travel_before_seconds FROM assignments WHERE status = 'published'")
    assert live == [("published", 1800)]


def test_at04_extension_rejects_stale_preview(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-at04")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550102", properties=["P-A"], key="cleaner-at04")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at04")
    preview = _preview(client, "2026-11-10", "preview-at04")
    original = next(item for item in _tasks(client) if item["deadline_at"])
    _sync(
        client,
        [
            _stay("R-A", "P-A", "2026-11-09T16:00:00-05:00", "2026-11-11T10:00:00-05:00", "2"),
            _stay("R-A-NEXT", "P-A", "2026-11-11T16:00:00-05:00", "2026-11-12T10:00:00-05:00", "2"),
        ],
        "sync-at04-extend",
    )
    revised = next(item for item in _tasks(client) if item["id"] == original["id"])
    assert revised["id"] == original["id"]
    assert str(revised["release_at"]).startswith("2026-11-11T15:00:00")
    stale = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
        headers={**_headers(MANAGER, "publish-stale"), "If-Match": '"1"'},
    )
    assert stale.status_code == 409
    assert _rows("SELECT count(*) FROM assignments")[0][0] == 0


def test_at06_last_minute_arrival_is_scheduled_after_travel(client: TestClient) -> None:
    _approve(client)
    _sync(client, [_stay("R-A", "P-A", "2026-11-09T16:00:00-05:00", "2026-11-10T10:00:00-05:00")], "sync-at06")
    pending = _tasks(client)
    assert pending[0]["deadline_at"] == "2026-11-12T15:00:00+00:00"
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550103", properties=["P-A"], key="cleaner-at06")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 1800})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", start="12:00:00", key="avail-at06")
    _sync(client, _turnover("R-A", "P-A", "2026-11-10")[:2][:1] and _turnover("R-A", "P-A", "2026-11-10"), "sync-at06b")
    ready = next(item for item in _tasks(client) if item["id"] == pending[0]["id"])
    assert str(ready["deadline_at"]).startswith("2026-11-10T20:30:00")
    preview = _preview(client, "2026-11-10", "preview-at06")
    placed = list(preview["candidate_plan"]["placed"])
    assert len(placed) == 1
    assert datetime.fromisoformat(str(placed[0]["start"])).astimezone(NY) == datetime(2026, 11, 10, 12, 30, tzinfo=NY)
    assert datetime.fromisoformat(str(placed[0]["end"])).astimezone(NY) == datetime(2026, 11, 10, 14, 30, tzinfo=NY)


def test_at08_day_specific_origins(client: TestClient) -> None:
    _approve(client)
    stays: list[dict[str, object]] = []
    stays.extend(_turnover("A10", "P-A", "2026-11-10"))
    stays.extend(_turnover("B10", "P-B", "2026-11-10"))
    stays.extend(_turnover("A11", "P-A", "2026-11-11"))
    stays.extend(_turnover("B11", "P-B", "2026-11-11"))
    _sync(client, stays, "sync-at08")
    first = _cleaner(client, name="Synthetic C-A", phone="+15555550104", properties=["P-A", "P-B"], key="c1-at08")
    second = _cleaner(client, name="Synthetic C-B", phone="+15555550105", properties=["P-A", "P-B"], key="c2-at08")
    far = 4 * 60 * 60
    set_travel_overrides(
        {
            ("synthetic:near-a", "P-A"): 60,
            ("synthetic:near-a", "P-B"): far,
            ("synthetic:near-b", "P-A"): far,
            ("synthetic:near-b", "P-B"): 60,
            ("P-A", "P-B"): far,
            ("P-B", "P-A"): far,
        }
    )
    _available(client, first, "2026-11-10", "synthetic:near-a", key="a10")
    _available(client, second, "2026-11-10", "synthetic:near-b", key="b10")
    _available(client, first, "2026-11-11", "synthetic:near-b", key="a11")
    _available(client, second, "2026-11-11", "synthetic:near-a", key="b11")
    day_one = _preview(client, "2026-11-10", "preview-d1")
    day_two = _preview(client, "2026-11-11", "preview-d2")
    assert _places(day_one, client) == {first: "P-A", second: "P-B"}
    assert _places(day_two, client) == {first: "P-B", second: "P-A"}
    missing = client.post(
        f"/v1/cleaners/{first}/availability",
        json={
            "service_date": "2026-11-12",
            "status": "available",
            "work_intervals": [{"start_at": "2026-11-12T09:00:00-05:00", "end_at": "2026-11-12T17:00:00-05:00"}],
            "unavailable_intervals": [],
            "origin_label": None,
            "expected_version": None,
        },
        headers=_headers(MANAGER, "missing-origin"),
    )
    assert missing.status_code == 422


def test_at10_long_travel_leaves_one_job_uncovered(client: TestClient) -> None:
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-at10")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550106", properties=["P-A", "P-B"], key="c-at10")
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 180 * 60,
            ("P-B", "P-A"): 180 * 60,
        }
    )
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at10")
    preview = _preview(client, "2026-11-10", "preview-at10")
    assert len(preview["candidate_plan"]["placed"]) == 1
    assert preview["uncovered"][0]["reason"] in {"NO_CAPACITY", "NO_ROUTE"}
    published = _publish(client, preview, "publish-at10")
    assert len(published["uncovered"]) == 1
    reason = published["uncovered"][0]["reason"]
    assert _rows("SELECT count(*) FROM exceptions WHERE code = :code", code=reason)[0][0] == 1


def test_at11_preview_is_silent_and_publish_is_idempotent(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("SMS send was called")

    monkeypatch.setattr(RecordingSmsGateway, "send", _forbidden)
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-at11")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550107", properties=["P-A"], key="c-at11")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at11")
    preview = _preview(client, "2026-11-10", "preview-at11")
    assert _rows("SELECT count(*) FROM outbox_events WHERE topic LIKE 'sms:%'")[0][0] == 0
    first = _publish(client, preview, "publish-at11")
    second = _publish(client, preview, "publish-at11")
    assert first == second
    assert _rows("SELECT count(*) FROM assignments WHERE status = 'published'")[0][0] == 1
    assert _rows("SELECT count(*) FROM outbox_events WHERE topic = 'schedule.published'")[0][0] == 1
    stale = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={"expected_fingerprint": preview["input_fingerprint"], "expected_day_revision": preview["day_revision"]},
        headers={**_headers(MANAGER, "publish-at11-stale"), "If-Match": '"1"'},
    )
    assert stale.status_code == 409


def test_at17_lock_survives_conflict_until_unlock(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10"), "sync-at17")
    cleaner = _cleaner(
        client,
        name="Synthetic C-A",
        phone="+15555550108",
        properties=["P-A", "P-B"],
        jobs=2,
        service=400,
        duty=500,
        key="c-at17",
    )
    set_travel_overrides(
        {("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-a", "P-B"): 60, ("P-A", "P-B"): 60, ("P-B", "P-A"): 60}
    )
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", end="20:00:00", key="avail-at17")
    preview = _preview(client, "2026-11-10", "preview-at17")
    _publish(client, preview, "publish-at17")
    rows = _rows(
        "SELECT id, version, planned_start, planned_end FROM assignments "
        "WHERE status = 'published' ORDER BY planned_start"
    )
    assert len(rows) == 2
    locked = client.post(
        f"/v1/assignments/{rows[0][0]}/lock",
        json={"locked": True, "reason": "manager hold"},
        headers={**_headers(MANAGER, "lock-at17"), "If-Match": f'"{rows[0][1]}"'},
    )
    assert locked.status_code == 200, locked.text
    conflict = client.post(
        f"/v1/assignments/{rows[1][0]}/override",
        json={
            "cleaner_id": cleaner,
            "planned_start": rows[0][2].isoformat(),
            "is_locked": False,
            "reason": "move into lock",
        },
        headers={**_headers(MANAGER, "override-conflict"), "If-Match": f'"{rows[1][1]}"'},
    )
    assert conflict.status_code == 422
    assert _rows("SELECT is_locked FROM assignments WHERE id = :id", id=rows[0][0])[0][0] is True
    unlocked = client.post(
        f"/v1/assignments/{rows[0][0]}/lock",
        json={"locked": False, "reason": "manager release"},
        headers={**_headers(MANAGER, "unlock-at17"), "If-Match": f'"{int(rows[0][1]) + 1}"'},
    )
    assert unlocked.status_code == 200, unlocked.text


def test_at24_dispatch_clock_is_idempotent_and_not_scheduled(client: TestClient) -> None:
    assert celery_app.conf.beat_schedule == {}
    _approve(client)
    settings = get_settings()
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    november = FrozenClock(datetime.fromisoformat("2026-11-01T23:00:00+00:00"))
    march = FrozenClock(datetime.fromisoformat("2027-03-14T22:00:00+00:00"))
    other = FrozenClock(datetime.fromisoformat("2026-11-01T22:00:00+00:00"))
    with factory() as session:
        assert dispatch_clock_tick(session, clock=other, settings=settings)["created"] is False
        first = dispatch_clock_tick(session, clock=november, settings=settings)
        session.commit()
        second = dispatch_clock_tick(session, clock=november, settings=settings)
        march_tick = dispatch_clock_tick(session, clock=march, settings=settings)
        session.commit()
    assert first["created"] is True
    assert first["published"] is False
    assert first["sms_sent"] is False
    assert first["intent_key"] == "dispatch:2026-11-02"
    assert second["created"] is False
    assert march_tick["intent_key"] == "dispatch:2027-03-15"
    assert _rows("SELECT count(*) FROM outbox_events WHERE topic = 'schedule.dispatch_clock'")[0][0] == 2


def test_at25_concurrent_publish_keeps_one_run(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-at25")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550109", properties=["P-A"], key="c-at25")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at25")
    preview = _preview(client, "2026-11-10", "preview-at25")
    settings = get_settings()
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)

    def _attempt() -> str:
        from uuid import UUID

        from app.services.schedules import publish_schedule
        from tests.conftest import TEST_CLOCK

        with factory() as session:
            try:
                publish_schedule(
                    session,
                    clock=TEST_CLOCK,
                    settings=settings,
                    run_id=UUID(str(preview["id"])),
                    expected_fingerprint=str(preview["input_fingerprint"]),
                    expected_day_revision=int(preview["day_revision"]),
                    expected_version=int(preview["version"]),
                    actor=MANAGER,
                )
            except AppError as exc:
                session.rollback()
                return exc.code
            session.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: _attempt(), (1, 2)))
    assert results.count("ok") == 1
    assert set(results) <= {"ok", "stale_preview", "stale_version"}
    assert _rows("SELECT count(*) FROM assignments WHERE status = 'published'")[0][0] == 1


def test_dispatch_clock_does_not_invent_policy(client: TestClient) -> None:
    settings = get_settings()
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as session:
        result = dispatch_clock_tick(
            session,
            clock=FrozenClock(datetime.fromisoformat("2026-11-01T23:00:00+00:00")),
            settings=settings,
        )
        session.commit()
    assert result["created"] is False
    assert _rows("SELECT count(*) FROM outbox_events")[0][0] == 0


def test_hundred_by_ten_preview_is_measured(client: TestClient) -> None:
    _approve(client)
    stays: list[dict[str, object]] = []
    for index in range(1, 101):
        external = f"synthetic-property-{index:03d}"
        stays.extend(_turnover(f"R-{index:03d}", external, "2026-12-01"))
    _sync(client, stays, "sync-load")
    ids = [f"synthetic-property-{index:03d}" for index in range(1, 101)]
    for index in range(1, 11):
        cleaner = _cleaner(
            client,
            name=f"Synthetic Cleaner {index:02d}",
            phone=f"+155555501{index:02d}",
            properties=ids,
            jobs=12,
            service=1440,
            duty=1440,
            key=f"load-cleaner-{index}",
        )
        _available(
            client,
            cleaner,
            "2026-12-01",
            f"synthetic:hub-{index}",
            start="08:00:00",
            end="18:00:00",
            key=f"load-avail-{index}",
        )
    started = datetime.now().timestamp()
    preview = _preview(client, "2026-12-01", "preview-load")
    elapsed = datetime.now().timestamp() - started
    assert elapsed < 30
    assert preview["solver_status"] == "feasible_not_proven_optimal"
    assert preview["sms_sent"] is False
    print(f"M4_LOAD_SECONDS {elapsed:.3f}")


def test_at07_unavailability_moves_unlocked_future_work(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10"), "sync-at07")
    first = _cleaner(client, name="Synthetic C-A", phone="+15555550201", properties=["P-A", "P-B"], key="c1-at07")
    second = _cleaner(client, name="Synthetic C-B", phone="+15555550202", properties=["P-A", "P-B"], key="c2-at07")
    set_travel_overrides(
        {
            ("synthetic:near-a", "P-A"): 60,
            ("synthetic:near-a", "P-B"): 60,
            ("synthetic:near-b", "P-A"): 120,
            ("synthetic:near-b", "P-B"): 120,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    availability = _available(client, first, "2026-11-10", "synthetic:near-a", key="a-at07")
    _available(client, second, "2026-11-10", "synthetic:near-b", start="13:00:00", key="b-at07")
    preview = _preview(client, "2026-11-10", "preview-at07")
    placed = list(preview["candidate_plan"]["placed"])
    assert len(placed) == 2
    assert {item["cleaner_id"] for item in placed} == {first}
    _publish(client, preview, "publish-at07")
    moved = client.post(
        f"/v1/cleaners/{first}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "unavailable",
            "work_intervals": [],
            "unavailable_intervals": [],
            "origin_label": None,
            "expected_version": availability["version"],
        },
        headers=_headers(MANAGER, "unavailable-at07"),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["sms_sent"] is False
    active = _rows(
        "SELECT cleaner_id::text FROM assignments WHERE status IN ('published','acknowledged') "
        "AND service_date = '2026-11-10'"
    )
    assert all(row[0] != first for row in active)
    assert active


def test_at15_replan_does_not_transfer_acknowledgment(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("A", "P-A", "2026-11-10"), "sync-at15")
    first = _cleaner(client, name="Synthetic C-A", phone="+15555550203", properties=["P-A"], key="c1-at15")
    second = _cleaner(client, name="Synthetic C-B", phone="+15555550204", properties=["P-A"], key="c2-at15")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 120})
    availability = _available(client, first, "2026-11-10", "synthetic:origin-a", key="a-at15")
    _available(client, second, "2026-11-10", "synthetic:origin-b", key="b-at15")
    preview = _preview(client, "2026-11-10", "preview-at15")
    _publish(client, preview, "publish-at15")
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as connection:
        connection.execute(
            text("UPDATE assignments SET status = 'acknowledged', acknowledged_at = now() WHERE status = 'published'")
        )
    moved = client.post(
        f"/v1/cleaners/{first}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "unavailable",
            "work_intervals": [],
            "unavailable_intervals": [],
            "origin_label": None,
            "expected_version": availability["version"],
        },
        headers=_headers(MANAGER, "unavailable-at15"),
    )
    assert moved.status_code == 200, moved.text
    historical = _rows("SELECT status, acknowledged_at IS NOT NULL FROM assignments WHERE cleaner_id = :id", id=first)
    assert historical == [("superseded", True)]
    current = _rows(
        "SELECT acknowledged_at IS NULL FROM assignments WHERE cleaner_id = :id AND status = 'published'",
        id=second,
    )
    assert current == [(True,)]


def test_at16_started_work_is_blocked_not_moved(client: TestClient) -> None:
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-at16")
    first = _cleaner(
        client,
        name="Synthetic C-A",
        phone="+15555550205",
        properties=["P-A", "P-B"],
        jobs=2,
        key="c1-at16",
    )
    second = _cleaner(client, name="Synthetic C-B", phone="+15555550206", properties=["P-A", "P-B"], key="c2-at16")
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("synthetic:origin-b", "P-A"): 180,
            ("synthetic:origin-b", "P-B"): 180,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    availability = _available(client, first, "2026-11-10", "synthetic:origin-a", end="20:00:00", key="a-at16")
    _available(client, second, "2026-11-10", "synthetic:origin-b", start="13:00:00", key="b-at16")
    preview = _preview(client, "2026-11-10", "preview-at16")
    placed = list(preview["candidate_plan"]["placed"])
    assert len(placed) == 2
    assert {item["cleaner_id"] for item in placed} == {first}
    _publish(client, preview, "publish-at16")
    client.app.state.clock = FrozenClock(
        min(datetime.fromisoformat(item["start"]) for item in placed) + timedelta(minutes=1)
    )
    _sync(client, stays, "refresh-at16")
    moved = client.post(
        f"/v1/cleaners/{first}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "unavailable",
            "work_intervals": [],
            "unavailable_intervals": [],
            "origin_label": None,
            "expected_version": availability["version"],
        },
        headers=_headers(MANAGER, "unavailable-at16"),
    )
    assert moved.status_code == 200, moved.text
    statuses = [row[0] for row in _rows("SELECT status FROM assignments WHERE cleaner_id = :id", id=first)]
    assert "blocked" in statuses
    assert "published" not in statuses


def test_at05_source_cancel_cancels_unstarted_assignment(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-at05")
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550208", properties=["P-A"], key="c-at05")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at05")
    preview = _preview(client, "2026-11-10", "preview-at05")
    _publish(client, preview, "publish-at05")
    canceled = _stay("R-A", "P-A", "2026-11-09T16:00:00-05:00", "2026-11-10T10:00:00-05:00", "2")
    canceled["status"] = "canceled"
    nxt = _stay("R-A-NEXT", "P-A", "2026-11-10T16:00:00-05:00", "2026-11-12T10:00:00-05:00", "2")
    _sync(client, [canceled, nxt], "sync-at05b")
    assert _rows("SELECT status FROM assignments")[0][0] == "canceled"


def test_at09_caps_and_breaks_are_enforced(client: TestClient) -> None:
    _approve(client)
    stays = (
        _turnover("A", "P-A", "2026-11-10")
        + _turnover("B", "P-B", "2026-11-10")
        + _turnover("C", "synthetic-property-001", "2026-11-10")
    )
    for external in ("P-A", "P-B", "synthetic-property-001"):
        patched = client.patch(
            f"/v1/properties/{_property_id(external)}",
            json={"duration_override_minutes": 120},
            headers={**_headers(MANAGER, f"duration-{external}"), "If-Match": '"1"'},
        )
        assert patched.status_code == 200, patched.text
    _sync(client, stays, "sync-at09")
    properties = ["P-A", "P-B", "synthetic-property-001"]
    first = _cleaner(
        client,
        name="Synthetic C-A",
        phone="+15555550209",
        properties=properties,
        jobs=1,
        service=150,
        key="c1-at09",
    )
    second = _cleaner(
        client,
        name="Synthetic C-B",
        phone="+15555550210",
        properties=properties,
        jobs=2,
        service=240,
        key="c2-at09",
    )
    points = ["synthetic:origin-a", "synthetic:origin-b", *properties]
    set_travel_overrides({(left, right): 60 for left in points for right in points if left != right})
    break_window = [{"start_at": "2026-11-10T12:00:00-05:00", "end_at": "2026-11-10T12:30:00-05:00"}]
    _available(
        client,
        first,
        "2026-11-10",
        "synthetic:origin-a",
        breaks=break_window,
        jobs=1,
        service=150,
        key="a-at09",
    )
    second_availability = _available(
        client,
        second,
        "2026-11-10",
        "synthetic:origin-b",
        breaks=break_window,
        jobs=2,
        service=240,
        duty=600,
        key="b-at09",
    )
    preview = _preview(client, "2026-11-10", "preview-at09")
    assert len(preview["candidate_plan"]["placed"]) == 3
    reduced = _available(
        client,
        second,
        "2026-11-10",
        "synthetic:origin-b",
        breaks=break_window,
        jobs=2,
        service=240,
        duty=100,
        key="b-at09-reduced",
        expected_version=int(second_availability["version"]),
    )
    assert reduced["version"] == 2
    again = _preview(client, "2026-11-10", "preview-at09b")
    assert len(again["candidate_plan"]["placed"]) < 3
    assert again["uncovered"]


def test_at28_five_bedroom_property_uses_formula_duration_and_override_marks_stale(client: TestClient) -> None:
    _approve(client)
    _sync(client, _turnover("B", "P-B", "2026-11-10"), "sync-at28")
    property_id = _property_id("P-B")
    bedrooms = client.patch(
        f"/v1/properties/{property_id}",
        json={"bedrooms": 5},
        headers={**_headers(MANAGER, "beds-at28"), "If-Match": '"1"'},
    )
    assert bedrooms.status_code == 200, bedrooms.text
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550211", properties=["P-B"], key="c-at28")
    set_travel_overrides({("synthetic:origin-a", "P-B"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at28")
    preview = _preview(client, "2026-11-10", "preview-at28")
    assert len(preview["candidate_plan"]["placed"]) == 1
    assert preview["uncovered"] == []
    tasks = client.get("/v1/tasks", headers=_headers(MANAGER, "tasks-at28")).json()["items"]
    five_bedroom_task = next(t for t in tasks if t["property_id"] == property_id)
    assert five_bedroom_task["duration_minutes"] == 210, "5 bedrooms should use formula: 60 + 30*5 = 210"
    assert five_bedroom_task["duration_source"] == "bedroom_default"
    restored = client.patch(
        f"/v1/properties/{property_id}",
        json={"duration_override_minutes": 120},
        headers={**_headers(MANAGER, "duration-at28"), "If-Match": '"2"'},
    )
    assert restored.status_code == 200, restored.text
    stale = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
        headers={**_headers(MANAGER, "publish-at28"), "If-Match": '"1"'},
    )
    assert stale.status_code == 409


def test_at27_manager_target_tightens_the_48_hour_deadline(client: TestClient) -> None:
    _approve(client)
    _sync(client, [_stay("R-A", "P-A", "2026-11-09T16:00:00-05:00", "2026-11-10T10:00:00-05:00")], "sync-at27")
    task = _tasks(client)[0]
    assert task["deadline_at"] == "2026-11-12T15:00:00+00:00"
    targeted = client.post(
        f"/v1/tasks/{task['id']}/target",
        json={"manager_target_at": "2026-11-10T15:30:00-05:00", "reason": "scenario target"},
        headers={**_headers(MANAGER, "target-at27"), "If-Match": f'"{task["version"]}"'},
    )
    assert targeted.status_code == 200, targeted.text
    assert targeted.json()["deadline_at"] == "2026-11-12T15:00:00+00:00"
    cleaner = _cleaner(client, name="Synthetic C-A", phone="+15555550207", properties=["P-A"], key="c-at27")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-at27")
    preview = _preview(client, "2026-11-10", "preview-at27")
    assert len(preview["candidate_plan"]["placed"]) == 1
    assert _tasks(client)[0]["deadline_at"] == "2026-11-12T15:00:00+00:00"


def _places(preview: dict[str, object], client: TestClient) -> dict[str, str]:
    tasks = {str(item["id"]): str(item["property_id"]) for item in _tasks(client)}
    properties = {row[0]: row[1] for row in _rows("SELECT id::text, external_id FROM properties")}
    placed = {}
    for item in preview["candidate_plan"]["placed"]:
        placed[str(item["cleaner_id"])] = properties[tasks[str(item["task_id"])]]
    return placed
