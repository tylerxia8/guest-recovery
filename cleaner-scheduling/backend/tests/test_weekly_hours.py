from datetime import date

from app.services.weekly_availability import effective_availability
from fastapi.testclient import TestClient
from tests.completed_fixture import db
from tests.test_scheduling import _approve, _available, _preview, _sync, _turnover
from tests.test_setup import cleaner, headers


def weekly(client: TestClient, cleaner_id: str, days: list[int], version: int = 0, key: str = "weekly"):
    return client.post(
        f"/v1/cleaners/{cleaner_id}/weekly-hours",
        headers=headers(key),
        json={
            "expected_version": version,
            "days": [
                {
                    "weekday": day,
                    "start_time": "09:00",
                    "end_time": "17:00",
                    "break_start": "12:00",
                    "break_end": "12:30",
                    "origin_label": "synthetic:setup",
                }
                for day in days
            ],
        },
    )


def test_weekly_hours_repeat_with_eastern_dst_and_dated_precedence(client: TestClient):
    row = cleaner(client)
    response = weekly(client, row, [0, 2])
    assert response.status_code == 200, response.text
    assert weekly(client, row, [0, 2]).json() == response.json()  # Idempotent replay.
    assert weekly(client, row, [1], key="stale").status_code == 409
    assert client.get(f"/v1/cleaners/{row}/weekly-hours", headers=headers(viewer=True)).status_code == 403
    for day, start in [(date(2026, 11, 11), "14:00:00+00:00"), (date(2027, 7, 7), "13:00:00+00:00")]:
        with db() as session:
            hours = effective_availability(session, day)
            assert len(hours) == 1
            assert hours[0].work_intervals[0]["start_at"].endswith(start)
            assert hours[0].unavailable_intervals
    _available(client, row, "2026-11-11", "synthetic:exception", start="10:00:00", key="dated")
    with db() as session:
        assert effective_availability(session, date(2026, 11, 11))[0].origin_label == "synthetic:exception"
        assert effective_availability(session, date(2026, 11, 12)) == []
    effective = client.get(f"/v1/cleaners/{row}/availability?service_date=2026-11-18", headers=headers()).json()
    assert effective["source"] == "weekly"
    assert effective["version"] == 0  # Dated exception can be created without a phantom row.


def test_weekly_hours_drive_solver_and_invalidate_preview(client: TestClient):
    _approve(client)
    _sync(client, _turnover("weekly-stay", "P-A", "2026-11-11"), "weekly-sync")
    row = cleaner(client)
    assert weekly(client, row, [2]).status_code == 200
    preview = _preview(client, "2026-11-11", "weekly-preview")
    assert len(preview["candidate_plan"]["placed"]) == 1
    assert weekly(client, row, [], version=1, key="clear-week").status_code == 200
    result = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        headers={**headers("old-preview"), "If-Match": '"1"'},
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
    )
    assert result.status_code == 409, result.text
    fresh = _preview(client, "2026-11-11", "no-hours-preview")
    assert fresh["candidate_plan"]["placed"] == []


def test_weekly_validation_and_dst_gap_never_guess_hours(client: TestClient):
    row = cleaner(client)
    assert weekly(client, row, [2, 2]).status_code == 422
    path = f"/v1/cleaners/{row}/weekly-hours"
    body = {
        "expected_version": 0,
        "days": [{"weekday": 6, "start_time": "01:00", "end_time": "02:30", "origin_label": "synthetic:setup"}],
    }
    assert client.post(path, headers=headers("dst-week"), json=body).status_code == 200
    with db() as session:
        assert effective_availability(session, date(2027, 3, 14)) == []
        assert effective_availability(session, date(2027, 11, 7)) == []
    body["expected_version"] = 1
    body["days"][0]["end_time"] = "00:30"
    assert client.post(path, headers=headers("inverted-week"), json=body).status_code == 422


def test_sms_unavailable_persists_exception_to_weekly_hours(client: TestClient):
    from app.models import Assignment
    from sqlalchemy import select
    from tests.test_scheduling import _publish
    from tests.test_sms import _inbound, _opt_in

    _approve(client)
    _sync(client, _turnover("weekly-sms", "P-A", "2026-11-11"), "weekly-sms-sync")
    row = cleaner(client)
    _opt_in(client, row)
    assert weekly(client, row, [2]).status_code == 200
    _publish(client, _preview(client, "2026-11-11", "weekly-sms-preview"), "weekly-sms-publish")
    _inbound(client, "+15555550919", "SM-WEEKLY-UNAVAILABLE", "UNAVAILABLE 2026-11-11")
    with db() as session:
        hours = effective_availability(session, date(2026, 11, 11))
        assert len(hours) == 1
        assert hours[0].status == "unavailable"
        assert effective_availability(session, date(2026, 11, 18))[0].status == "available"
        assert all(a.status == "superseded" for a in session.scalars(select(Assignment)))


def test_manager_unavailable_overrides_weekly_without_reentering_hours(client: TestClient):
    from tests.test_scheduling import _publish

    _approve(client)
    _sync(client, _turnover("weekly-manager", "P-A", "2026-11-11"), "weekly-manager-sync")
    row = cleaner(client)
    assert weekly(client, row, [2]).status_code == 200
    _publish(client, _preview(client, "2026-11-11", "manager-preview"), "manager-publish")
    result = client.post(
        f"/v1/cleaners/{row}/availability",
        headers=headers("manager-unavailable"),
        json={
            "service_date": "2026-11-11",
            "status": "unavailable",
            "expected_version": 0,
            "work_intervals": [],
            "unavailable_intervals": [],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["replanned"] is True
    with db() as session:
        assert effective_availability(session, date(2026, 11, 11))[0].status == "unavailable"
