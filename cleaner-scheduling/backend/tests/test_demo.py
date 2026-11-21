from __future__ import annotations

import uuid

import pytest
from app.api.app_factory import create_app
from app.clock import FrozenClock
from app.config import get_settings
from app.demo.server import DEMO_NOW, build_demo_app, guard
from fastapi.testclient import TestClient
from tests.completed_fixture import complete_fixture
from tests.support import auth_headers


def demo_settings():
    settings = get_settings()
    return settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://cleaning_app@127.0.0.1:55439/cleaning_demo",
            "migration_database_url": "postgresql+psycopg://cleaning_migrator@127.0.0.1:55439/cleaning_demo",
            "redis_url": "redis://127.0.0.1:6398/7",
        }
    )


def test_demo_requires_explicit_isolation(monkeypatch):
    monkeypatch.delenv("LOCAL_DEMO", raising=False)
    with pytest.raises(RuntimeError):
        guard(demo_settings())
    monkeypatch.setenv("LOCAL_DEMO", "1")
    guard(demo_settings())
    for changes in [
        {"database_url": get_settings().database_url},
        {"redis_url": "redis://127.0.0.1:6398/0"},
        {"production_dispatch_enabled": True, "production_integrations_enabled": True},
        {"database_url": "postgresql+psycopg://app@remote/cleaning_demo"},
        {"migration_database_url": "postgresql+psycopg://app@127.0.0.1:55439/cleaning"},
    ]:
        with pytest.raises(RuntimeError):
            guard(demo_settings().model_copy(update=changes))


def test_standard_app_never_exposes_demo_session(client):
    assert client.post("/v1/demo/session", json={"role": "owner"}).status_code == 404
    assert client.get("/v1/demo/housing").status_code == 404


def test_demo_reply_rbac_replay_and_conflict(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.demo.housing.HOUSING_FILE", tmp_path / "housing.json")
    complete_fixture(client, two=True)
    monkeypatch.setenv("LOCAL_DEMO", "1")
    # Exercise mounted demo routes against the ordinary isolated Pytest database.
    # The real guard still validates dedicated settings before the test app is injected.
    settings = get_settings()
    monkeypatch.setattr(
        "app.demo.server.create_app", lambda _settings, _clock: create_app(settings, FrozenClock(DEMO_NOW))
    )
    app = build_demo_app(demo_settings())
    with TestClient(app) as demo:
        viewer = auth_headers("synthetic-viewer", roles=("viewer",))
        manager = auth_headers("synthetic-manager", roles=("manager",))
        assert demo.get("/v1/demo/housing", headers=viewer).status_code == 403
        assert demo.get("/v1/demo/housing", headers=manager).json() is None
        assert demo.get("/v1/demo/assignments", headers=viewer).status_code == 403
        rows = demo.get("/v1/demo/assignments", headers=manager).json()["items"]
        body = {"assignment_id": rows[0]["id"], "command": "CONFIRM", "event_id": str(uuid.uuid4())}
        assert demo.post("/v1/demo/reply", json=body, headers=viewer).status_code == 403
        first = demo.post("/v1/demo/reply", json=body, headers=manager)
        assert first.status_code == 200, first.text
        assert demo.post("/v1/demo/reply", json=body, headers=manager).json() == first.json()
        assert demo.post("/v1/demo/reply", json={**body, "command": "DONE"}, headers=manager).status_code == 409
        assert demo.post("/v1/demo/record-sms", headers=viewer).status_code == 403
        session = demo.post("/v1/demo/session", json={"role": "manager"})
        assert session.status_code == 200
        assert (
            demo.get(
                "/v1/dashboard?service_date=2026-11-10", headers={"Authorization": f"Bearer {session.json()['token']}"}
            ).status_code
            == 200
        )

    with TestClient(app, client=("203.0.113.10", 50000)) as remote:
        assert remote.post("/v1/demo/session", json={"role": "owner"}).status_code == 403


def test_synthetic_source_clock_is_repeatable():
    from app.adapters.reservations.mock import MockFeed, MockReservationProvider, set_mock_feed

    set_mock_feed(MockFeed(pages=[[], []], observed_at=DEMO_NOW))
    provider = MockReservationProvider()
    first = provider.pull()
    assert first.source_as_of == DEMO_NOW
    assert provider.pull(cursor=first.next_cursor).source_as_of == DEMO_NOW
