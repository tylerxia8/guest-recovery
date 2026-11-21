from __future__ import annotations

import json

from fastapi.testclient import TestClient
from tests.support import auth_headers, count_rows, valid_settings


def test_same_key_and_body_replays_one_policy(client: TestClient) -> None:
    headers = auth_headers("synthetic-owner", key="same-key")
    body = {"settings": valid_settings(), "reason": "Record proposed defaults."}
    first = client.post("/v1/policy-versions", json=body, headers=headers)
    second = client.post("/v1/policy-versions", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert count_rows("policy_versions") == 1
    assert count_rows("idempotency_records") == 1


def test_key_order_does_not_change_digest(client: TestClient) -> None:
    settings = valid_settings()
    reason = "Record proposed defaults."
    headers = auth_headers("synthetic-owner", key="order-key")
    headers["Content-Type"] = "application/json"
    first_raw = json.dumps({"reason": reason, "settings": settings}, separators=(",", ":"))
    second_raw = json.dumps({"settings": settings, "reason": reason}, separators=(",", ":"))
    first = client.post("/v1/policy-versions", content=first_raw, headers=headers)
    second = client.post("/v1/policy-versions", content=second_raw, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert count_rows("policy_versions") == 1


def test_same_key_different_body_conflicts(client: TestClient) -> None:
    headers = auth_headers("synthetic-owner", key="conflict-key")
    first_body = {"settings": valid_settings(), "reason": "First reason."}
    changed = valid_settings()
    changed["readiness_buffer_minutes"] = 45
    second_body = {"settings": changed, "reason": "Second reason."}
    first = client.post("/v1/policy-versions", json=first_body, headers=headers)
    second = client.post("/v1/policy-versions", json=second_body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"
    assert count_rows("policy_versions") == 1


def test_unknown_transfer_field_is_rejected(client: TestClient) -> None:
    settings = valid_settings()
    settings["bank_transfer"] = True
    response = client.post(
        "/v1/policy-versions",
        json={"settings": settings, "reason": "Try to enable a transfer."},
        headers=auth_headers("synthetic-owner", key="transfer"),
    )
    assert response.status_code == 422
    assert count_rows("policy_versions") == 0
    assert count_rows("outbox_events") == 0


def test_missing_ack_reassignment_and_five_bedrooms_are_rejected(client: TestClient) -> None:
    reassign = valid_settings()
    reassign["auto_reassign_missing_ack"] = True
    first = client.post(
        "/v1/policy-versions",
        json={"settings": reassign, "reason": "Enable reassignment."},
        headers=auth_headers("synthetic-owner", key="reassign"),
    )
    durations = valid_settings()
    durations["duration_minutes_by_bedrooms"] = {"0": 60, "1": 90, "2": 120, "3": 150, "4": 180, "5": 240}
    second = client.post(
        "/v1/policy-versions",
        json={"settings": durations, "reason": "Add five bedrooms."},
        headers=auth_headers("synthetic-owner", key="beds"),
    )
    assert first.status_code == 422
    assert second.status_code == 422
    assert count_rows("policy_versions") == 0


def test_schedule_preview_idempotent_replay(client: TestClient) -> None:
    """Two sequential same-key POST /schedules/previews requests replay the stored result.

    The second request finds the existing idempotency record and returns the stored response.
    This tests the normal replay path where no race occurs.
    """
    from tests.support import valid_settings

    created = client.post(
        "/v1/policy-versions",
        json={"settings": valid_settings(), "reason": "Setup for preview test."},
        headers=auth_headers("synthetic-owner", key="preview-policy-create"),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/v1/policy-versions/{created.json()['id']}/approve",
        json={"approval_note": "Enable scheduling.", "effective_at": "2026-11-01T00:00:00Z"},
        headers={**auth_headers("synthetic-owner", key="preview-policy-approve"), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text

    headers = auth_headers("synthetic-owner", key="preview-same-key", roles=("manager",))
    body = {"service_date": "2026-11-15"}

    first = client.post("/v1/schedules/previews", json=body, headers=headers)
    assert first.status_code == 200, first.text
    first_id = first.json()["id"]

    second = client.post("/v1/schedules/previews", json=body, headers=headers)
    assert second.status_code == 200, f"Expected 200 on replay, got {second.status_code}: {second.text}"
    assert second.json()["id"] == first_id, "Replayed response should have same run ID"

    assert count_rows("idempotency_records") >= 1


def test_schedule_preview_concurrent_integrity_error(client: TestClient) -> None:
    """Concurrent same-key requests race past idempotency check, one catches IntegrityError.

    Both requests call run_preview_solver before either stores the idempotency record.
    This forces both to attempt INSERT, causing IntegrityError for the second.
    The IntegrityError handler should rollback and replay the stored result.

    MUST fail on 5e47fdb (pre-IntegrityError-handling) with 500 or IntegrityError.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from tests.support import valid_settings

    created = client.post(
        "/v1/policy-versions",
        json={"settings": valid_settings(), "reason": "Setup for concurrent preview test."},
        headers=auth_headers("synthetic-owner", key="concurrent-policy-create"),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/v1/policy-versions/{created.json()['id']}/approve",
        json={"approval_note": "Enable scheduling.", "effective_at": "2026-11-01T00:00:00Z"},
        headers={**auth_headers("synthetic-owner", key="concurrent-policy-approve"), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text

    headers = auth_headers("synthetic-owner", key="concurrent-preview-key", roles=("manager",))
    body = {"service_date": "2026-11-16"}

    barrier = threading.Barrier(2, timeout=10)
    call_count = 0

    from app.api.routes import schedules as schedules_route

    original_solver = schedules_route.run_preview_solver

    def barrier_wrapper(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        result = original_solver(*args, **kwargs)
        barrier.wait()
        return result

    results: list[dict[str, object]] = []

    def make_request() -> dict[str, object]:
        resp = client.post("/v1/schedules/previews", json=body, headers=headers)
        return {"status": resp.status_code, "body": resp.json() if resp.status_code < 500 else resp.text}

    with patch.object(schedules_route, "run_preview_solver", barrier_wrapper):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(make_request) for _ in range(2)]
            results = [f.result() for f in futures]

    assert call_count == 2, f"Barrier wrapper should be called twice, got {call_count}"

    statuses = [r["status"] for r in results]
    assert all(s == 200 for s in statuses), f"Both requests should return 200, got {statuses}: {results}"

    ids = [r["body"]["id"] for r in results if isinstance(r["body"], dict)]
    assert len(ids) == 2, f"Expected 2 results with IDs, got {ids}"
    assert len(set(ids)) == 1, f"Both should return same run ID, got {ids}"

    import os

    from app.db import get_engine
    from sqlalchemy import text

    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM idempotency_records WHERE key LIKE :pattern"),
            {"pattern": "%concurrent-preview-key%"},
        ).scalar_one()
    assert count == 1, f"Should have exactly one idempotency record for the preview key, got {count}"


def test_approval_requires_if_match_and_is_idempotent(client: TestClient) -> None:
    created = client.post(
        "/v1/policy-versions",
        json={"settings": valid_settings(), "reason": "Record proposed defaults."},
        headers=auth_headers("synthetic-owner", key="create"),
    )
    assert created.status_code == 201
    policy_id = created.json()["id"]
    approve_body = {
        "approval_note": "Approve tunable defaults. This does not enable dispatch.",
        "effective_at": "2026-11-08T00:00:00+00:00",
    }
    missing = client.post(
        f"/v1/policy-versions/{policy_id}/approve",
        json=approve_body,
        headers=auth_headers("synthetic-owner", key="approve-missing"),
    )
    assert missing.status_code == 428
    stale = client.post(
        f"/v1/policy-versions/{policy_id}/approve",
        json=approve_body,
        headers={**auth_headers("synthetic-owner", key="approve-stale"), "If-Match": "9"},
    )
    assert stale.status_code == 409
    approved = client.post(
        f"/v1/policy-versions/{policy_id}/approve",
        json=approve_body,
        headers={**auth_headers("synthetic-owner", key="approve"), "If-Match": '"1"'},
    )
    assert approved.status_code == 200
    assert approved.json()["is_active"] is True
    assert approved.json()["approved_by"] == "synthetic-owner"
    replay = client.post(
        f"/v1/policy-versions/{policy_id}/approve",
        json=approve_body,
        headers={**auth_headers("synthetic-owner", key="approve"), "If-Match": '"1"'},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == policy_id
    assert count_rows("policy_versions") == 1
    assert count_rows("outbox_events") == 0
