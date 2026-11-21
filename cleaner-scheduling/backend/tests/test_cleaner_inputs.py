import uuid

from app.models import Cleaner, CleanerWeeklyHours
from app.services.field_cipher import reveal_text
from app.services.starting_address import capture_starting_routes
from fastapi.testclient import TestClient
from tests.completed_fixture import db
from tests.test_driving_baseline import fixture_baseline
from tests.test_scheduling import _approve, _available, _preview, _property_id, _publish, _sync, _turnover
from tests.test_setup import cleaner, headers


def test_formatted_phone_and_hours_based_capacity(client: TestClient):
    _approve(client)
    _sync(client, _turnover("simple-A", "P-A", "2026-11-11"), "simple-sync")
    body = {
        "display_name": "Example cleaner",
        "phone": "(704) 555-0123",
        "eligibility_property_ids": [_property_id("P-A")],
    }
    result = client.post("/v1/cleaners", headers=headers("simple-create"), json=body)
    assert result.status_code == 201, result.text
    row = result.json()
    assert row["max_jobs"] is row["max_service_minutes"] is row["max_duty_minutes"] is None
    with db() as session:
        saved = session.get(Cleaner, uuid.UUID(row["id"]))
        assert reveal_text(saved.phone_ciphertext, client.app.state.settings.auth_mock_secret) == "+17045550123"
    body["phone"] = "+1 704-555-0123"
    assert client.post("/v1/cleaners", headers=headers("duplicate-format"), json=body).status_code == 409
    body["phone"] = "704-12"
    assert client.post("/v1/cleaners", headers=headers("bad-phone"), json=body).status_code == 422
    _available(client, row["id"], "2026-11-11", "synthetic:setup", key="simple-hours")
    run = _preview(client, "2026-11-11", "simple-plan")
    assert len(run["candidate_plan"]["placed"]) == 1, run["uncovered"]


def test_typed_address_routes_are_private_cached_and_used_by_publication(client: TestClient, monkeypatch):
    _approve(client)
    _sync(client, _turnover("address-A", "P-A", "2026-11-11"), "address-sync")
    row = cleaner(client)
    baseline = fixture_baseline()
    client.app.state.session_factory.configure(
        info={**client.app.state.session_factory.kw["info"], "driving_baseline": baseline}
    )
    calls = []

    def capture(address, saved_baseline):
        calls.append(address)
        return {"status": "ready", "baseline_id": saved_baseline.digest, "seconds": {"P-A": 2400}}

    monkeypatch.setattr("app.api.routes.setup.capture_starting_routes", capture)
    path = f"/v1/cleaners/{row}/weekly-hours"
    body = {
        "expected_version": 0,
        "starting_address": "123 Fictional Lane, Charlotte, NC",
        "days": [{"weekday": 2, "start_time": "09:00", "end_time": "17:00"}],
    }
    response = client.post(path, json=body, headers=headers("address-save"))
    assert response.status_code == 200, response.text
    assert "Fictional Lane" not in response.text
    assert client.get(path, headers=headers()).json()["starting_address"] == body["starting_address"]
    assert client.get(path, headers=headers(viewer=True)).status_code == 403
    body["expected_version"] = 1
    assert client.post(path, json=body, headers=headers("cached-address")).status_code == 200
    assert len(calls) == 1
    run = _preview(client, "2026-11-11", "address-plan")
    assert run["candidate_plan"]["placed"][0]["travel_before_seconds"] == 2400
    _publish(client, run, "address-publish")
    dashboard = client.get("/v1/dashboard?service_date=2026-11-11", headers=headers(viewer=True)).json()
    assert dashboard["today"][0]["travel_from"] == "Cleaner starting address"
    assert "Fictional Lane" not in str(dashboard)
    with db() as session:
        saved = session.get(CleanerWeeklyHours, uuid.UUID(row))
        assert b"Fictional Lane" not in saved.starting_address_ciphertext
        saved.origin_routes = {"status": "address_not_found"}
        session.commit()
    missing = _preview(client, "2026-11-11", "missing-address-plan")
    assert missing["candidate_plan"]["placed"] == []


def test_capture_reuses_known_property_and_validates_online_results(monkeypatch):
    baseline = fixture_baseline({"P-A": {"property_id": str(uuid.uuid4()), "name": "123 Fictional Lane"}})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Known saved addresses must not trigger online lookup")

    monkeypatch.setattr("app.services.starting_address._get", forbidden)
    result = capture_starting_routes("123 Fictional Lane", baseline)
    assert result["status"] == "ready"
    responses = iter(
        [
            {"result": {"addressMatches": [{"coordinates": {"x": -80.8, "y": 35.1}}]}},
            {"code": "Ok", "sources": [{"distance": 5}], "destinations": [{"distance": 4}], "durations": [[123.4]]},
        ]
    )
    monkeypatch.setattr("app.services.starting_address._get", lambda *_args, **_kwargs: next(responses))
    assert capture_starting_routes("456 Fictional Lane", baseline)["seconds"] == {"P-A": 124}
    monkeypatch.setattr(
        "app.services.starting_address._get", lambda *_args, **_kwargs: {"result": {"addressMatches": []}}
    )
    assert capture_starting_routes("Unknown address", baseline)["status"] == "address_not_found"
