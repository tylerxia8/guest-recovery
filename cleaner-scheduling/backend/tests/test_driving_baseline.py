from datetime import UTC, datetime, timedelta

import pytest
from app.adapters.routing.baseline import DrivingBaseline
from app.models import Property, RoutingSnapshot
from pydantic import ValidationError
from sqlalchemy import select
from tests.completed_fixture import db
from tests.test_scheduling import _approve, _available, _cleaner, _preview, _publish, _sync, _turnover
from tests.test_setup import demo_app, headers


def fixture_baseline(bindings=None):
    return DrivingBaseline(
        schema_version=1,
        captured_at=datetime(2026, 11, 7, tzinfo=UTC),
        source_url="https://example.invalid/recorded-routes",
        coordinate_source_url="https://example.invalid/points",
        staging_name="Fictional public staging",
        staging_source_url="https://example.invalid/staging",
        points=["P-A", "P-B", "staging"],
        seconds=[[0, 120, 60], [1800, 0, 600], [60, 600, 0]],
        coordinates={"P-A": [0, 0], "P-B": [1, 1], "staging": [2, 2]},
        origin_aliases={"synthetic:baseline-origin": "staging"},
        property_bindings=bindings or {},
        max_snap_distance_meters=10,
    )


def test_cached_matrix_is_directional_offline_and_unknowns_are_unavailable(monkeypatch):
    baseline = fixture_baseline()
    matrix = baseline.matrix(("P-A", "P-B", "synthetic:baseline-origin", "unknown"))
    assert matrix.seconds_or_unreachable[0][1] == 120
    assert matrix.seconds_or_unreachable[1][0] == 1800
    assert matrix.seconds_or_unreachable[2][0] == 60
    assert matrix.seconds_or_unreachable[3] == (None, None, None, None)
    assert matrix.expires_at == baseline.captured_at + timedelta(microseconds=1)
    assert DrivingBaseline.model_validate_json(baseline.model_dump_json()).digest == baseline.digest
    for changes in ({"seconds": [[0]]}, {"seconds": [[0, -1, 0]] * 3}, {"origin_aliases": {"bad": "missing"}}):
        with pytest.raises(ValidationError):
            DrivingBaseline.model_validate({**baseline.model_dump(), **changes})


def prepare(client):
    _approve(client)
    stays = _turnover("base-A", "P-A", "2026-11-10") + _turnover("base-B", "P-B", "2026-11-10")
    stays[1]["checkin_at"] = "2026-11-10T15:00:00-05:00"
    _sync(client, stays, "base-sync")
    cleaner = _cleaner(
        client, name="Synthetic baseline cleaner", phone="+15555550929", properties=["P-A", "P-B"], key="base-cleaner"
    )
    _available(client, cleaner, "2026-11-10", "synthetic:baseline-origin", key="base-availability")
    client.app.state.session_factory.configure(
        info={**client.app.state.session_factory.kw["info"], "driving_baseline": fixture_baseline()}
    )


def test_saved_driving_controls_sequence_time_and_persisted_dashboard(client, monkeypatch):
    prepare(client)

    def forbidden(_name):
        raise AssertionError("Cached scheduling must not call the mock or online router")

    monkeypatch.setattr("app.services.schedules.build_routing_service", forbidden)
    preview = _preview(client, "2026-11-10", "base-preview")
    placed = sorted(preview["candidate_plan"]["placed"], key=lambda row: row["start"])
    assert len(placed) == 2
    assert [row["travel_before_seconds"] for row in placed] == [60, 120]
    assert datetime.fromisoformat(placed[1]["start"]) - datetime.fromisoformat(placed[0]["end"]) >= timedelta(
        seconds=120
    )
    _publish(client, preview, "base-publish")
    body = client.get("/v1/dashboard?service_date=2026-11-10", headers=headers()).json()
    assert body["daily_travel_seconds"] == 180
    assert {row["travel_source"] for row in body["today"]} == {"osrm_cached"}
    assert {row["travel_before_seconds"] for row in body["today"]} == {60, 120}
    with db() as session:
        snapshot = session.scalar(select(RoutingSnapshot).where(RoutingSnapshot.provider == "osrm_cached"))
        assert snapshot.matrix_seconds["provenance"]["traffic"] == "none"


def test_baseline_change_rejects_old_preview(client):
    prepare(client)
    preview = _preview(client, "2026-11-10", "old-baseline")
    baseline = fixture_baseline()
    baseline.seconds[0][1] = 180
    client.app.state.session_factory.configure(
        info={**client.app.state.session_factory.kw["info"], "driving_baseline": baseline}
    )
    response = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
        headers={**headers("stale-baseline"), "If-Match": '"1"'},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_preview"


def test_missing_saved_road_route_is_uncovered_without_synthetic_fallback(client):
    prepare(client)
    baseline = fixture_baseline()
    for i in range(3):
        if i != 1:
            baseline.seconds[i][1] = None
            baseline.seconds[1][i] = None
    client.app.state.session_factory.configure(
        info={**client.app.state.session_factory.kw["info"], "driving_baseline": baseline}
    )
    preview = _preview(client, "2026-11-10", "missing-saved-route")
    assert len(preview["candidate_plan"]["placed"]) == 1
    assert preview["uncovered"]
    _publish(client, preview, "missing-saved-publish")
    body = client.get("/v1/dashboard?service_date=2026-11-10", headers=headers()).json()
    assert body["unassigned_count"] == 1


def test_capture_reuses_saved_online_response_without_another_request(monkeypatch, tmp_path):
    import importlib.util
    import io
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "infra/local/build_driving_baseline.py"
    spec = importlib.util.spec_from_file_location("capture_routes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(b'{"code":"Ok","durations":[[0,120],[240,0]]}')

    monkeypatch.setattr(module, "urlopen", fetch)
    first = module.capture("https://example.invalid/table")
    assert module.capture("https://example.invalid/table") == first
    assert calls == [1]


def test_demo_loader_rejects_stale_house_bindings(client, monkeypatch, tmp_path):
    with db() as session:
        prop = session.scalar(select(Property).where(Property.external_id == "P-A"))
        binding = {"P-A": {"property_id": str(prop.id), "name": prop.name + " changed"}}
    path = tmp_path / "baseline.json"
    path.write_text(fixture_baseline(binding).model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("LOCAL_DEMO_ROUTING_BASELINE", "1")
    monkeypatch.setattr("app.demo.routing.BASELINE_FILE", path)
    with pytest.raises(RuntimeError, match="does not match"):
        demo_app(monkeypatch)
