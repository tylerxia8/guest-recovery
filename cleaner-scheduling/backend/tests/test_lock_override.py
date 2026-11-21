"""Tests for manager lock/override operations with hard-constraint enforcement.

These tests verify:
- Lock conflicts surface as exceptions and block infeasible plans
- Stale If-Match returns 409
- Viewer (non-manager) cannot lock/unlock/override
- Locked assignments survive replanning
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.adapters.reservations.mock import MockFeed, set_mock_feed
from app.adapters.routing.mock import set_travel_overrides
from app.config import get_settings
from app.db import session_factory
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.support import auth_headers, valid_settings

NY = ZoneInfo("America/New_York")
MANAGER = "synthetic-manager"
OWNER = "synthetic-owner"
VIEWER = "synthetic-viewer"


def _headers(subject: str, key: str, roles: tuple[str, ...] = ("manager",)) -> dict[str, str]:
    return auth_headers(subject, key=key, roles=roles)


def _approve(client: TestClient) -> None:
    created = client.post(
        "/v1/policy-versions",
        json={"settings": valid_settings(), "reason": "Test fixture."},
        headers=_headers(OWNER, "policy-create", ("owner",)),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/v1/policy-versions/{created.json()['id']}/approve",
        json={"approval_note": "Test fixture only.", "effective_at": "2026-11-01T00:00:00Z"},
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


def _rows(query: str, **params: object) -> list[tuple[object, ...]]:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        return list(connection.execute(text(query), params).all())


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
    previous = datetime.fromisoformat(f"{day}T12:00:00-05:00") - timedelta(days=1)
    previous_str = previous.date().isoformat() + "T16:00:00-05:00"
    next_day = datetime.fromisoformat(f"{day}T12:00:00-05:00") + timedelta(days=1)
    next_str = next_day.date().isoformat() + "T10:00:00-05:00"
    return [
        _stay(external, prop, previous_str, f"{day}T10:00:00-05:00", revision),
        _stay(f"{external}-NEXT", prop, f"{day}T16:00:00-05:00", next_str, revision),
    ]


def _cleaner(client: TestClient, *, name: str, phone: str, properties: list[str], key: str) -> str:
    response = client.post(
        "/v1/cleaners",
        json={
            "display_name": name,
            "phone": phone,
            "max_jobs": 2,
            "max_service_minutes": 300,
            "max_duty_minutes": 420,
            "eligibility_property_ids": [_property_id(item) for item in properties],
        },
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _available(client: TestClient, cleaner_id: str, day: str, origin: str, key: str) -> None:
    response = client.post(
        f"/v1/cleaners/{cleaner_id}/availability",
        json={
            "service_date": day,
            "status": "available",
            "work_intervals": [{"start_at": f"{day}T09:00:00-05:00", "end_at": f"{day}T17:00:00-05:00"}],
            "unavailable_intervals": [],
            "origin_label": origin,
            "expected_version": None,
        },
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 200, response.text


def _preview(client: TestClient, day: str, key: str) -> dict[str, object]:
    response = client.post(
        "/v1/schedules/previews",
        json={"service_date": day},
        headers=_headers(MANAGER, key),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _publish(client: TestClient, preview: dict[str, object], key: str) -> dict[str, object]:
    response = client.post(
        f"/v1/schedules/runs/{preview['id']}/publish",
        json={
            "expected_fingerprint": preview["input_fingerprint"],
            "expected_day_revision": preview["day_revision"],
        },
        headers={**_headers(MANAGER, key), "If-Match": '"1"'},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_viewer_cannot_lock_assignment(client: TestClient) -> None:
    """AT-23 subset: viewers cannot lock assignments."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-viewer-lock")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551001", properties=["P-A"], key="c-viewer")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-viewer")
    preview = _preview(client, "2026-11-10", "preview-viewer")
    _publish(client, preview, "publish-viewer")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    response = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": True, "reason": "viewer attempt"},
        headers={**_headers(VIEWER, "viewer-lock", ("viewer",)), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


def test_viewer_cannot_override_assignment(client: TestClient) -> None:
    """AT-23 subset: viewers cannot reassign."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-viewer-override")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551002", properties=["P-A"], key="c-viewer-o")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-viewer-o")
    preview = _preview(client, "2026-11-10", "preview-viewer-o")
    _publish(client, preview, "publish-viewer-o")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner,
            "planned_start": "2026-11-10T10:00:00-05:00",
            "is_locked": False,
            "reason": "viewer attempt",
        },
        headers={**_headers(VIEWER, "viewer-override", ("viewer",)), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


def test_stale_if_match_returns_409_for_lock(client: TestClient) -> None:
    """Stale If-Match version returns 409."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-stale-lock")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551003", properties=["P-A"], key="c-stale")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-stale")
    preview = _preview(client, "2026-11-10", "preview-stale")
    _publish(client, preview, "publish-stale")

    assignment_id = _rows("SELECT id FROM assignments WHERE status = 'published' LIMIT 1")[0][0]

    # First lock succeeds with version 1
    first = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": True, "reason": "first lock"},
        headers={**_headers(MANAGER, "lock-first"), "If-Match": '"1"'},
    )
    assert first.status_code == 200

    # Second attempt with stale version 1 returns 409
    stale = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": False, "reason": "stale attempt"},
        headers={**_headers(MANAGER, "lock-stale"), "If-Match": '"1"'},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"


def test_override_into_locked_time_is_rejected(client: TestClient) -> None:
    """Override into locked time slot is rejected with infeasible error.

    Scenario: Two tasks assigned to same cleaner at different times. Lock the first.
    Try to override the second to overlap the first - this should fail with
    infeasible because it would conflict with the locked assignment.
    """
    _approve(client)
    _sync(client, _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10"), "sync-conflict")
    cleaner = _cleaner(
        client,
        name="Cleaner A",
        phone="+15555551004",
        properties=["P-A", "P-B"],
        key="c-conflict",
    )
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-conflict")
    preview = _preview(client, "2026-11-10", "preview-conflict")
    _publish(client, preview, "publish-conflict")

    rows = _rows(
        "SELECT id, version, planned_start, planned_end, cleaner_id FROM assignments "
        "WHERE status = 'published' ORDER BY planned_start"
    )
    assert len(rows) == 2, f"Expected 2 assignments, got {len(rows)}"
    first_id, first_version, first_start, first_end, first_cleaner = rows[0]
    second_id, second_version, second_start, second_end, second_cleaner = rows[1]

    # Lock the first assignment
    locked = client.post(
        f"/v1/assignments/{first_id}/lock",
        json={"locked": True, "reason": "protect this slot"},
        headers={**_headers(MANAGER, "lock-conflict"), "If-Match": f'"{first_version}"'},
    )
    assert locked.status_code == 200

    # Verify lock is applied
    lock_check = _rows(
        "SELECT is_locked FROM assignments WHERE id = :id",
        id=first_id,
    )[0][0]
    assert lock_check is True, "First assignment should be locked"

    # Try to override the second assignment to be assigned to the same cleaner
    # at the same time as the locked first assignment
    conflict = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": str(first_cleaner),  # Same cleaner as first assignment
            "planned_start": first_start.isoformat(),  # Overlapping start time
            "is_locked": False,
            "reason": "move into locked slot",
        },
        headers={**_headers(MANAGER, "override-conflict"), "If-Match": f'"{second_version}"'},
    )
    assert conflict.status_code == 422, conflict.text
    assert conflict.json()["error"]["code"] == "infeasible"
    assert "overlaps" in conflict.json()["error"]["message"].lower()

    # Second assignment should remain unchanged
    second_status = _rows(
        "SELECT status, cleaner_id FROM assignments WHERE id = :id",
        id=second_id,
    )[0]
    assert second_status[0] == "published", "Second assignment should remain published"


def test_locked_assignment_cannot_be_reassigned_without_unlock(client: TestClient) -> None:
    """Locked assignments must be unlocked before override."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-locked-override")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551005", properties=["P-A"], key="c-locked-o")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-locked-o")
    preview = _preview(client, "2026-11-10", "preview-locked-o")
    _publish(client, preview, "publish-locked-o")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    # Lock the assignment
    locked = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": True, "reason": "protect this"},
        headers={**_headers(MANAGER, "lock-before-override"), "If-Match": f'"{version}"'},
    )
    assert locked.status_code == 200

    # Try to override the locked assignment
    override_attempt = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner,
            "planned_start": "2026-11-10T11:00:00-05:00",
            "is_locked": False,
            "reason": "try to move locked",
        },
        headers={**_headers(MANAGER, "override-locked"), "If-Match": f'"{int(version) + 1}"'},
    )
    assert override_attempt.status_code == 409
    assert override_attempt.json()["error"]["code"] == "locked"


def test_lock_preserves_assignment_across_replan(client: TestClient) -> None:
    """Locked assignments are preserved when new schedules are published."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-preserve-lock")

    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551006", properties=["P-A"], key="c-preserve-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551007", properties=["P-A"], key="c-preserve-b")

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-b", "P-A"): 60,
        }
    )
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-preserve-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:origin-b", key="avail-preserve-b")

    preview1 = _preview(client, "2026-11-10", "preview-preserve-1")
    _publish(client, preview1, "publish-preserve-1")

    # Lock the assignment to cleaner A
    assignment_id, version, cleaner_id = _rows(
        "SELECT id, version, cleaner_id FROM assignments WHERE status = 'published' LIMIT 1"
    )[0]

    locked = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": True, "reason": "must be cleaner A"},
        headers={**_headers(MANAGER, "lock-preserve"), "If-Match": f'"{version}"'},
    )
    assert locked.status_code == 200

    # Generate and publish a new schedule preview
    preview2 = _preview(client, "2026-11-10", "preview-preserve-2")
    publish2 = client.post(
        f"/v1/schedules/runs/{preview2['id']}/publish",
        json={
            "expected_fingerprint": preview2["input_fingerprint"],
            "expected_day_revision": preview2["day_revision"],
        },
        headers={**_headers(MANAGER, "publish-preserve-2"), "If-Match": '"1"'},
    )
    assert publish2.status_code == 200

    # The locked assignment should still exist and be published
    locked_assignment = _rows(
        "SELECT status, is_locked, cleaner_id FROM assignments WHERE id = :id",
        id=assignment_id,
    )[0]
    assert locked_assignment[0] == "published", "Locked assignment should remain published"
    assert locked_assignment[1] is True, "Assignment should still be locked"
    assert locked_assignment[2] == cleaner_id, "Locked assignment should keep same cleaner"


def test_dashboard_includes_lock_fields(client: TestClient) -> None:
    """Dashboard API includes lock information for managers."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-dashboard-lock")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551008", properties=["P-A"], key="c-dash-lock")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-dash-lock")
    preview = _preview(client, "2026-11-10", "preview-dash-lock")
    _publish(client, preview, "publish-dash-lock")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    # Lock the assignment
    locked = client.post(
        f"/v1/assignments/{assignment_id}/lock",
        json={"locked": True, "reason": "test lock reason"},
        headers={**_headers(MANAGER, "lock-dash"), "If-Match": f'"{version}"'},
    )
    assert locked.status_code == 200

    # Check dashboard includes lock info
    dashboard = client.get(
        "/v1/dashboard?service_date=2026-11-10",
        headers=_headers(MANAGER, "dash-check"),
    )
    assert dashboard.status_code == 200
    data = dashboard.json()

    task_with_assignment = next((t for t in data["today"] if t.get("assignment_id")), None)
    assert task_with_assignment is not None
    assert task_with_assignment["is_locked"] is True
    assert task_with_assignment["lock_reason"] == "test lock reason"
    assert task_with_assignment["assignment_version"] is not None


def test_override_ineligible_cleaner_rejected(client: TestClient) -> None:
    """Override to a cleaner ineligible for the property returns 422."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-inelig")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551009", properties=["P-A"], key="c-inelig-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551010", properties=["P-B"], key="c-inelig-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-inelig-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:origin-b", key="avail-inelig-b")
    preview = _preview(client, "2026-11-10", "preview-inelig")
    _publish(client, preview, "publish-inelig")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T10:00:00-05:00",
            "is_locked": False,
            "reason": "ineligible cleaner",
        },
        headers={**_headers(MANAGER, "override-inelig"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ineligible_cleaner"


def test_override_outside_availability_rejected(client: TestClient) -> None:
    """Override with start outside cleaner's work hours returns 422."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-outside")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551011", properties=["P-A"], key="c-outside-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551017", properties=["P-A"], key="c-outside-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-outside-a")
    response = client.post(
        f"/v1/cleaners/{cleaner_b}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "available",
            "work_intervals": [{"start_at": "2026-11-10T14:00:00-05:00", "end_at": "2026-11-10T17:00:00-05:00"}],
            "unavailable_intervals": [],
            "origin_label": "synthetic:origin-b",
            "expected_version": None,
        },
        headers=_headers(MANAGER, "avail-outside-b"),
    )
    assert response.status_code == 200, response.text
    preview = _preview(client, "2026-11-10", "preview-outside")
    _publish(client, preview, "publish-outside")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T10:00:00-05:00",
            "is_locked": False,
            "reason": "too early for shift",
        },
        headers={**_headers(MANAGER, "override-outside"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "outside_shift"


def test_override_exceeds_max_jobs_rejected(client: TestClient) -> None:
    """Override that would exceed max_jobs limit returns 422."""
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-maxjobs")

    cleaner_one = client.post(
        "/v1/cleaners",
        json={
            "display_name": "Cleaner One",
            "phone": "+15555551012",
            "max_jobs": 1,
            "max_service_minutes": 600,
            "max_duty_minutes": 600,
            "eligibility_property_ids": [_property_id("P-A"), _property_id("P-B")],
        },
        headers=_headers(MANAGER, "c-maxjobs-one"),
    )
    assert cleaner_one.status_code == 201, cleaner_one.text
    cleaner_one_id = cleaner_one.json()["id"]

    cleaner_two = _cleaner(
        client, name="Cleaner Two", phone="+15555551016", properties=["P-A", "P-B"], key="c-maxjobs-two"
    )
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("synthetic:origin-b", "P-A"): 60,
            ("synthetic:origin-b", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_one_id, "2026-11-10", "synthetic:origin-a", key="avail-maxjobs-one")
    _available(client, cleaner_two, "2026-11-10", "synthetic:origin-b", key="avail-maxjobs-two")
    preview = _preview(client, "2026-11-10", "preview-maxjobs")
    _publish(client, preview, "publish-maxjobs")

    one_assignment = _rows(
        "SELECT id FROM assignments WHERE status = 'published' AND cleaner_id = :cid",
        cid=cleaner_one_id,
    )
    assert len(one_assignment) == 1, f"Cleaner one should have 1 assignment, got {len(one_assignment)}"

    two_assignment = _rows(
        "SELECT id, version FROM assignments WHERE status = 'published' AND cleaner_id = :cid",
        cid=cleaner_two,
    )
    assert len(two_assignment) == 1, f"Cleaner two should have 1 assignment, got {len(two_assignment)}"

    second_id, second_version = two_assignment[0]

    one_times = _rows(
        "SELECT planned_start, planned_end FROM assignments WHERE cleaner_id = :cid AND status = 'published'",
        cid=cleaner_one_id,
    )
    assert len(one_times) == 1, f"Expected 1 assignment for cleaner one, got {len(one_times)}"
    one_end = one_times[0][1]
    later_start = (one_end + timedelta(hours=1)).astimezone(NY).strftime("%Y-%m-%dT%H:%M:%S%z")
    later_start = later_start[:-2] + ":" + later_start[-2:]

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner_one_id,
            "planned_start": later_start,
            "is_locked": False,
            "reason": "too many jobs",
        },
        headers={**_headers(MANAGER, "override-maxjobs"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "exceeds_max_jobs"


def test_override_completed_assignment_rejected(client: TestClient) -> None:
    """Completed assignments cannot be overridden."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-completed")
    cleaner = _cleaner(client, name="Cleaner A", phone="+15555551013", properties=["P-A"], key="c-completed")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-completed")
    preview = _preview(client, "2026-11-10", "preview-completed")
    _publish(client, preview, "publish-completed")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as conn:
        conn.execute(
            text("UPDATE assignments SET status = 'completed', completed_at = NOW() WHERE id = :id"),
            {"id": assignment_id},
        )

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner,
            "planned_start": "2026-11-10T10:00:00-05:00",
            "is_locked": False,
            "reason": "override completed",
        },
        headers={**_headers(MANAGER, "override-completed"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "terminal_assignment"


def test_concurrent_override_same_version_race(client: TestClient) -> None:
    """Concurrent overrides on the same version: only one should succeed."""
    import threading
    import time

    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-race")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551014", properties=["P-A"], key="c-race-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551015", properties=["P-A"], key="c-race-b")
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-b", "P-A"): 60,
        }
    )
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-race-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:origin-b", key="avail-race-b")
    preview = _preview(client, "2026-11-10", "preview-race")
    _publish(client, preview, "publish-race")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]

    results: list[int] = []
    lock = threading.Lock()

    def try_override(target_cleaner: str, key_suffix: str) -> None:
        response = client.post(
            f"/v1/assignments/{assignment_id}/override",
            json={
                "cleaner_id": target_cleaner,
                "planned_start": "2026-11-10T11:00:00-05:00",
                "is_locked": False,
                "reason": f"race-{key_suffix}",
            },
            headers={**_headers(MANAGER, f"override-race-{key_suffix}"), "If-Match": f'"{version}"'},
        )
        with lock:
            results.append(response.status_code)

    t1 = threading.Thread(target=try_override, args=(cleaner_a, "a"))
    t2 = threading.Thread(target=try_override, args=(cleaner_b, "b"))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()

    success_count = results.count(200)
    stale_count = results.count(409)
    assert success_count == 1, f"Exactly one should succeed, got {results}"
    assert stale_count == 1, f"Exactly one should fail with stale version, got {results}"


def test_override_overlapping_break_rejected(client: TestClient) -> None:
    """Override that overlaps a cleaner's break interval returns 422."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-break")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551020", properties=["P-A"], key="c-break-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551021", properties=["P-A"], key="c-break-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-break-a")
    response = client.post(
        f"/v1/cleaners/{cleaner_b}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "available",
            "work_intervals": [{"start_at": "2026-11-10T09:00:00-05:00", "end_at": "2026-11-10T17:00:00-05:00"}],
            "unavailable_intervals": [{"start_at": "2026-11-10T12:00:00-05:00", "end_at": "2026-11-10T13:00:00-05:00"}],
            "origin_label": "synthetic:origin-b",
            "expected_version": None,
        },
        headers=_headers(MANAGER, "avail-break-b"),
    )
    assert response.status_code == 200, response.text
    preview = _preview(client, "2026-11-10", "preview-break")
    _publish(client, preview, "publish-break")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]
    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T11:30:00-05:00",
            "is_locked": False,
            "reason": "conflicts with break",
        },
        headers={**_headers(MANAGER, "override-break"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "conflicts_with_break"


def test_override_insufficient_travel_rejected(client: TestClient) -> None:
    """Override with insufficient travel time from predecessor returns 422."""
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-travel")
    cleaner = _cleaner(client, name="Cleaner One", phone="+15555551022", properties=["P-A", "P-B"], key="c-travel")
    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 1800,
            ("P-B", "P-A"): 1800,
        }
    )
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-travel")
    preview = _preview(client, "2026-11-10", "preview-travel")
    _publish(client, preview, "publish-travel")

    sql = """SELECT id, version, planned_start, planned_end
              FROM assignments WHERE cleaner_id = :cid AND status = 'published'
              ORDER BY planned_start"""
    assignments = _rows(sql, cid=cleaner)
    assert len(assignments) >= 2, f"Need 2 assignments, got {len(assignments)}"
    second_id, second_version, _, _ = assignments[1]
    first_end = assignments[0][3]
    too_soon = (first_end + timedelta(minutes=5)).astimezone(NY).strftime("%Y-%m-%dT%H:%M:%S%z")
    too_soon = too_soon[:-2] + ":" + too_soon[-2:]

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner,
            "planned_start": too_soon,
            "is_locked": False,
            "reason": "too soon after first job",
        },
        headers={**_headers(MANAGER, "override-travel"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "insufficient_travel"


def test_override_unreachable_destination_rejected(client: TestClient) -> None:
    """Override to unreachable destination returns 422."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-unreachable")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551023", properties=["P-A"], key="c-unreachable-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551024", properties=["P-A"], key="c-unreachable-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-unreachable-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:unreachable-origin", key="avail-unreachable-b")
    preview = _preview(client, "2026-11-10", "preview-unreachable")
    _publish(client, preview, "publish-unreachable")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]
    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T11:00:00-05:00",
            "is_locked": False,
            "reason": "unreachable origin",
        },
        headers={**_headers(MANAGER, "override-unreachable"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unreachable"


def test_override_exceeds_duty_cap_rejected(client: TestClient) -> None:
    """Override that exceeds duty cap returns 422.

    Cleaner has a duty cap (600 min). We publish 2 jobs to them, then
    after publishing, reduce their duty cap so that trying to reschedule
    one of them much later would exceed the cap.
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-duty")
    cleaner_id = _cleaner(
        client, name="Cleaner Duty", phone="+15555551025", properties=["P-A", "P-B"], key="c-duty-one"
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-duty-one")
    preview = _preview(client, "2026-11-10", "preview-duty")
    _publish(client, preview, "publish-duty")

    sql = """SELECT id, version, planned_start, planned_end
              FROM assignments WHERE cleaner_id = :cid AND status = 'published'
              ORDER BY planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    if len(assignments) < 2:
        pytest.skip(f"Need 2 assignments for this cleaner, got {len(assignments)}")

    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as conn:
        conn.execute(
            text("UPDATE cleaners SET max_duty_minutes = 120 WHERE id = :id"),
            {"id": cleaner_id},
        )

    first_id, first_version, first_start, first_end = assignments[0]
    _, _, second_start, second_end = assignments[1]
    span_minutes = int((second_end - first_start).total_seconds() / 60)
    if span_minutes < 120:
        pytest.skip(f"Span too short ({span_minutes} min) to test duty cap")

    response = client.post(
        f"/v1/assignments/{first_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": first_start.astimezone(NY).isoformat(),
            "is_locked": False,
            "reason": "re-override same time - still triggers cap",
        },
        headers={**_headers(MANAGER, "override-duty"), "If-Match": f'"{first_version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "exceeds_duty_cap"


def test_override_exceeds_service_cap_rejected(client: TestClient) -> None:
    """Override that exceeds service cap returns 422.

    Cleaner has 2 jobs and we reduce their service cap so that keeping both would exceed it.
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-service")
    cleaner_id = _cleaner(
        client, name="Cleaner Service", phone="+15555551027", properties=["P-A", "P-B"], key="c-service-one"
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-service-one")
    preview = _preview(client, "2026-11-10", "preview-service")
    _publish(client, preview, "publish-service")

    sql = """SELECT id, version, planned_start, duration_minutes
              FROM assignments WHERE cleaner_id = :cid AND status = 'published'
              ORDER BY planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    if len(assignments) < 2:
        pytest.skip(f"Need 2 assignments for this cleaner, got {len(assignments)}")

    first_id, first_version, first_start, first_duration = assignments[0]
    _, _, _, second_duration = assignments[1]
    total_service = (first_duration or 0) + (second_duration or 0)

    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as conn:
        conn.execute(
            text("UPDATE cleaners SET max_service_minutes = :cap WHERE id = :id"),
            {"id": cleaner_id, "cap": total_service - 10},
        )

    response = client.post(
        f"/v1/assignments/{first_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": first_start.astimezone(NY).isoformat(),
            "is_locked": False,
            "reason": "re-override same time - triggers service cap",
        },
        headers={**_headers(MANAGER, "override-service"), "If-Match": f'"{first_version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "exceeds_service_cap"


def test_override_superseded_assignment_rejected(client: TestClient) -> None:
    """Superseded assignments return 409 terminal_assignment."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-superseded")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551029", properties=["P-A"], key="c-superseded-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551030", properties=["P-A"], key="c-superseded-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-superseded-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:origin-b", key="avail-superseded-b")
    preview = _preview(client, "2026-11-10", "preview-superseded")
    _publish(client, preview, "publish-superseded")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]
    first_override = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T11:00:00-05:00",
            "is_locked": False,
            "reason": "first override",
        },
        headers={**_headers(MANAGER, "override-superseded-1"), "If-Match": f'"{version}"'},
    )
    assert first_override.status_code == 200, first_override.text

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_a,
            "planned_start": "2026-11-10T12:00:00-05:00",
            "is_locked": False,
            "reason": "second override on superseded",
        },
        headers={**_headers(MANAGER, "override-superseded-2"), "If-Match": f'"{version + 1}"'},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "terminal_assignment"


def test_override_canceled_assignment_rejected(client: TestClient) -> None:
    """Canceled assignments return 409 terminal_assignment."""
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-canceled")
    cleaner_a = _cleaner(client, name="Cleaner A", phone="+15555551031", properties=["P-A"], key="c-canceled-a")
    cleaner_b = _cleaner(client, name="Cleaner B", phone="+15555551032", properties=["P-A"], key="c-canceled-b")
    set_travel_overrides({("synthetic:origin-a", "P-A"): 60, ("synthetic:origin-b", "P-A"): 60})
    _available(client, cleaner_a, "2026-11-10", "synthetic:origin-a", key="avail-canceled-a")
    _available(client, cleaner_b, "2026-11-10", "synthetic:origin-b", key="avail-canceled-b")
    preview = _preview(client, "2026-11-10", "preview-canceled")
    _publish(client, preview, "publish-canceled")

    assignment_id, version = _rows("SELECT id, version FROM assignments WHERE status = 'published' LIMIT 1")[0]
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as conn:
        conn.execute(text("UPDATE assignments SET status = 'canceled' WHERE id = :id"), {"id": assignment_id})

    response = client.post(
        f"/v1/assignments/{assignment_id}/override",
        json={
            "cleaner_id": cleaner_b,
            "planned_start": "2026-11-10T11:00:00-05:00",
            "is_locked": False,
            "reason": "override canceled",
        },
        headers={**_headers(MANAGER, "override-canceled"), "If-Match": f'"{version}"'},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "terminal_assignment"


def test_override_duty_cap_uses_first_house_origin_travel(client: TestClient) -> None:
    """Duty cap origin travel is computed to the day's first house, not the override target.

    Setup: Cleaner has 2 jobs scheduled. P-A is earlier (first house), P-B is later.
    Origin travel to P-A is 60s, origin travel to P-B is 1800s.
    When overriding P-B's assignment, duty cap should use the 60s travel (to first house P-A),
    not the 1800s travel (to override target P-B).
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    stays[1]["checkin_at"] = "2026-11-10T15:00:00-05:00"
    _sync(client, stays, "sync-duty-first")
    cleaner_id = _cleaner(
        client, name="Cleaner Duty First", phone="+15555551040", properties=["P-A", "P-B"], key="c-duty-first"
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 1800,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-duty-first")
    preview = _preview(client, "2026-11-10", "preview-duty-first")
    _publish(client, preview, "publish-duty-first")

    sql = """SELECT id, version, planned_start, planned_end
              FROM assignments WHERE cleaner_id = :cid AND status = 'published'
              ORDER BY planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    if len(assignments) < 2:
        pytest.skip(f"Need 2 assignments, got {len(assignments)}")

    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    first_id, _, first_start, first_end = assignments[0]
    second_id, second_version, second_start, second_end = assignments[1]

    span_seconds = (second_end - first_start).total_seconds()
    duty_limit_with_first_house_travel = int(span_seconds / 60) + 1 + 5

    with factory.begin() as conn:
        conn.execute(
            text("UPDATE cleaners SET max_duty_minutes = :cap WHERE id = :id"),
            {"id": cleaner_id, "cap": duty_limit_with_first_house_travel},
        )

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": second_start.astimezone(NY).isoformat(),
            "is_locked": False,
            "reason": "should pass with first house travel",
        },
        headers={**_headers(MANAGER, "override-duty-first"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 200, f"Expected success but got {response.text}"


def test_override_unreachable_origin_to_first_house_rejected(client: TestClient) -> None:
    """Override where origin travel to the day's first house is unreachable returns 422.

    Setup: Cleaner has 2 jobs (P-A early, P-B late). Origin travel to P-A is reachable.
    After publishing, we make origin→P-A unreachable and try to override P-B.
    The duty cap check fails because origin travel to first house (P-A) is unreachable.
    This returns 422 even when the cleaner has no duty cap (intentional fail-closed).
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-unreachable-first")
    cleaner_id = _cleaner(
        client,
        name="Cleaner Unreachable First",
        phone="+15555551041",
        properties=["P-A", "P-B"],
        key="c-unreachable-first",
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-unreachable-first")
    preview = _preview(client, "2026-11-10", "preview-unreachable-first")
    _publish(client, preview, "publish-unreachable-first")

    sql = """SELECT a.id, a.version, a.planned_start, a.planned_end, p.external_id
              FROM assignments a
              JOIN cleaning_tasks ct ON a.task_id = ct.id
              JOIN properties p ON ct.property_id = p.id
              WHERE a.cleaner_id = :cid AND a.status = 'published'
              ORDER BY a.planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    assert len(assignments) >= 2, f"Need 2 assignments for cleaner, got {len(assignments)}"

    first_id, _, first_start, first_end, first_prop = assignments[0]
    second_id, second_version, second_start, second_end, second_prop = assignments[1]

    schedule_version_sql = "SELECT revision FROM schedule_days WHERE service_date = '2026-11-10'"
    schedule_before = _rows(schedule_version_sql)
    assignments_before = _rows(sql, cid=cleaner_id)

    set_travel_overrides(
        {
            ("synthetic:origin-a", first_prop): None,
            ("synthetic:origin-a", second_prop): 60,
            (first_prop, second_prop): 60,
            (second_prop, first_prop): 60,
        }
    )

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": second_start.astimezone(NY).isoformat(),
            "is_locked": False,
            "reason": "try override when origin to first house is unreachable",
        },
        headers={**_headers(MANAGER, "override-unreachable-first"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unreachable"

    schedule_after = _rows(schedule_version_sql)
    assignments_after = _rows(sql, cid=cleaner_id)
    assert schedule_after == schedule_before, "Schedule version should not change on failure"
    assert len(assignments_after) == len(assignments_before), "Assignment count should not change"
    assert assignments_after == assignments_before, "Assignments should not change on failure"


def test_override_missing_predecessor_property_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Override fails with 422 when predecessor's property lookup returns None.

    Setup: Cleaner has 2 jobs (P-A early, P-B late). Override P-B to start after P-A ends.
    Monkeypatch session.get to return None for the predecessor's property lookup.
    This tests schedules.py:402-404, distinct from the first-assignment branch at :432-435.
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-missing-pred")
    cleaner_id = _cleaner(
        client,
        name="Cleaner Missing Pred",
        phone="+15555551045",
        properties=["P-A", "P-B"],
        key="c-missing-pred",
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-missing-pred")
    preview = _preview(client, "2026-11-10", "preview-missing-pred")
    _publish(client, preview, "publish-missing-pred")

    sql = """SELECT a.id, a.version, a.planned_start, a.planned_end, p.external_id, ct.property_id
              FROM assignments a
              JOIN cleaning_tasks ct ON a.task_id = ct.id
              JOIN properties p ON ct.property_id = p.id
              WHERE a.cleaner_id = :cid AND a.status = 'published'
              ORDER BY a.planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    assert len(assignments) >= 2, f"Need 2 assignments, got {len(assignments)}"

    first_id, _, first_start, first_end, first_prop, first_prop_uuid = assignments[0]
    second_id, second_version, second_start, second_end, second_prop, _ = assignments[1]

    new_start = (first_end + timedelta(minutes=5)).astimezone(NY)

    schedule_version_sql = "SELECT revision FROM schedule_days WHERE service_date = '2026-11-10'"
    schedule_before = _rows(schedule_version_sql)
    assignments_before = _rows(sql, cid=cleaner_id)

    from app.models.tables import Property

    original_session_get = None

    def mock_session_get(self, model, id_val):
        if model is Property and str(id_val) == str(first_prop_uuid):
            return None
        return original_session_get(self, model, id_val)

    from sqlalchemy.orm import Session

    original_session_get = Session.get
    monkeypatch.setattr(Session, "get", mock_session_get)

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": new_start.isoformat(),
            "is_locked": False,
            "reason": "override after predecessor with missing property",
        },
        headers={**_headers(MANAGER, "override-missing-pred"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "missing_predecessor"
    assert error["message"] == "Predecessor assignment has no valid property."

    monkeypatch.undo()
    schedule_after = _rows(schedule_version_sql)
    assignments_after = _rows(sql, cid=cleaner_id)
    assert schedule_after == schedule_before, "Schedule version should not change on failure"
    assert len(assignments_after) == len(assignments_before), "Assignment count should not change"
    assert assignments_after == assignments_before, "Assignments should not change on failure"


def test_override_missing_first_assignment_property_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Override fails with 422 when first assignment's property lookup returns None.

    Setup: Cleaner has 2 jobs (P-A early, P-B late). Override P-B at its current time.
    P-A is both the predecessor AND the first assignment.
    Property lookups for P-A happen in order:
      1. _collect_routing_points (line 473) - gracefully skips if None
      2. predecessor check (line 402) - would fail if None
      3. first-assignment check (line 432) - should fail if None
    We return None on 3rd+ lookup to skip past the predecessor check and hit first-assignment.
    This tests schedules.py:432-435, distinct from the predecessor branch at :402-404.
    """
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    _sync(client, stays, "sync-missing-first")
    cleaner_id = _cleaner(
        client,
        name="Cleaner Missing First",
        phone="+15555551046",
        properties=["P-A", "P-B"],
        key="c-missing-first",
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 60,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    _available(client, cleaner_id, "2026-11-10", "synthetic:origin-a", key="avail-missing-first")
    preview = _preview(client, "2026-11-10", "preview-missing-first")
    _publish(client, preview, "publish-missing-first")

    sql = """SELECT a.id, a.version, a.planned_start, a.planned_end, p.external_id, ct.property_id
              FROM assignments a
              JOIN cleaning_tasks ct ON a.task_id = ct.id
              JOIN properties p ON ct.property_id = p.id
              WHERE a.cleaner_id = :cid AND a.status = 'published'
              ORDER BY a.planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    assert len(assignments) >= 2, f"Need 2 assignments, got {len(assignments)}"

    first_id, _, first_start, first_end, first_prop, first_prop_uuid = assignments[0]
    second_id, second_version, second_start, second_end, second_prop, _ = assignments[1]

    schedule_version_sql = "SELECT revision FROM schedule_days WHERE service_date = '2026-11-10'"
    schedule_before = _rows(schedule_version_sql)
    assignments_before = _rows(sql, cid=cleaner_id)

    from app.models.tables import Property

    original_session_get = None
    call_count = {"property": 0}

    def mock_session_get(self, model, id_val):
        if model is Property and str(id_val) == str(first_prop_uuid):
            call_count["property"] += 1
            if call_count["property"] >= 3:
                return None
        return original_session_get(self, model, id_val)

    from sqlalchemy.orm import Session

    original_session_get = Session.get
    monkeypatch.setattr(Session, "get", mock_session_get)

    response = client.post(
        f"/v1/assignments/{second_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": second_start.astimezone(NY).isoformat(),
            "is_locked": False,
            "reason": "override when first assignment property is missing",
        },
        headers={**_headers(MANAGER, "override-missing-first"), "If-Match": f'"{second_version}"'},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "missing_predecessor"
    assert error["message"] == "First assignment has no valid property."

    monkeypatch.undo()
    schedule_after = _rows(schedule_version_sql)
    assignments_after = _rows(sql, cid=cleaner_id)
    assert schedule_after == schedule_before, "Schedule version should not change on failure"
    assert len(assignments_after) == len(assignments_before), "Assignment count should not change"
    assert assignments_after == assignments_before, "Assignments should not change on failure"


def _early_turnover(
    external: str, prop: str, day: str, checkout_hour: int, revision: str = "1"
) -> list[dict[str, object]]:
    """Create a turnover pair with custom checkout time."""
    previous = datetime.fromisoformat(f"{day}T12:00:00-05:00") - timedelta(days=1)
    previous_str = previous.date().isoformat() + "T16:00:00-05:00"
    next_day = datetime.fromisoformat(f"{day}T12:00:00-05:00") + timedelta(days=1)
    next_str = next_day.date().isoformat() + "T10:00:00-05:00"
    checkout_str = f"{day}T{checkout_hour:02d}:00:00-05:00"
    return [
        _stay(external, prop, previous_str, checkout_str, revision),
        _stay(f"{external}-NEXT", prop, f"{day}T16:00:00-05:00", next_str, revision),
    ]


def test_override_becomes_new_first_job(client: TestClient) -> None:
    """Override that becomes the new first job uses origin travel to new first house.

    Setup: Cleaner has 2 jobs. P-A checks out at 10am, P-B checks out at 8am.
    Solver places P-A first (10am-11:30), P-B second (around 11:30-13:00).
    We override P-B to 8:30am, making it the new first job.
    Duty cap uses origin travel to P-B (60s), not P-A (1800s).
    """
    _approve(client)
    stays_a = _early_turnover("R-NEW-FIRST-A", "P-A", "2026-11-10", 10)
    stays_b = _early_turnover("R-NEW-FIRST-B", "P-B", "2026-11-10", 8)
    _sync(client, stays_a + stays_b, "sync-new-first")
    cleaner_id = _cleaner(
        client,
        name="Cleaner New First",
        phone="+15555551047",
        properties=["P-A", "P-B"],
        key="c-new-first",
    )

    set_travel_overrides(
        {
            ("synthetic:origin-a", "P-A"): 1800,
            ("synthetic:origin-a", "P-B"): 60,
            ("P-A", "P-B"): 60,
            ("P-B", "P-A"): 60,
        }
    )
    response = client.post(
        f"/v1/cleaners/{cleaner_id}/availability",
        json={
            "service_date": "2026-11-10",
            "status": "available",
            "work_intervals": [{"start_at": "2026-11-10T08:00:00-05:00", "end_at": "2026-11-10T17:00:00-05:00"}],
            "unavailable_intervals": [],
            "origin_label": "synthetic:origin-a",
            "expected_version": None,
        },
        headers=_headers(MANAGER, "avail-new-first"),
    )
    assert response.status_code == 200, response.text
    preview = _preview(client, "2026-11-10", "preview-new-first")
    _publish(client, preview, "publish-new-first")

    sql = """SELECT a.id, a.version, a.planned_start, a.planned_end, p.external_id
              FROM assignments a
              JOIN cleaning_tasks ct ON a.task_id = ct.id
              JOIN properties p ON ct.property_id = p.id
              WHERE a.cleaner_id = :cid AND a.status = 'published'
              ORDER BY a.planned_start"""
    assignments = _rows(sql, cid=cleaner_id)
    assert len(assignments) >= 2, f"Solver regression: need 2 assignments, got {len(assignments)}"

    by_prop = {a[4]: a for a in assignments}
    assert "P-A" in by_prop, f"Solver regression: no P-A assignment. Got: {list(by_prop.keys())}"
    assert "P-B" in by_prop, f"Solver regression: no P-B assignment. Got: {list(by_prop.keys())}"

    pb_assignment = by_prop["P-B"]
    pa_assignment = by_prop["P-A"]

    pb_id, pb_version, pb_start, pb_end, _ = pb_assignment
    _, _, pa_start, pa_end, _ = pa_assignment

    pb_duration = (pb_end - pb_start).total_seconds()
    new_pb_start = pa_start - timedelta(seconds=pb_duration) - timedelta(minutes=5)
    new_pb_start = new_pb_start.astimezone(NY)

    span_with_new_order = (pa_end - new_pb_start).total_seconds()
    duty_limit = int(span_with_new_order / 60) + 1 + 10

    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as conn:
        conn.execute(
            text("UPDATE cleaners SET max_duty_minutes = :cap WHERE id = :id"),
            {"id": cleaner_id, "cap": duty_limit},
        )

    response = client.post(
        f"/v1/assignments/{pb_id}/override",
        json={
            "cleaner_id": cleaner_id,
            "planned_start": new_pb_start.isoformat(),
            "is_locked": False,
            "reason": "move P-B to become new first job before P-A",
        },
        headers={**_headers(MANAGER, "override-new-first"), "If-Match": f'"{pb_version}"'},
    )
    assert response.status_code == 200, f"Expected success but got {response.text}"
