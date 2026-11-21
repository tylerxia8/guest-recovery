from __future__ import annotations

import io
import uuid

from app.adapters.reservations.fixtures import FIXTURE_CLEANERS, PROPERTY_COUNT
from app.adapters.reservations.mock import MockFeed, set_mock_feed
from app.config import get_settings
from app.db import get_engine, session_factory
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.support import audit_blob, auth_headers, count_rows

MANAGER = "synthetic-manager"


def _headers(key: str) -> dict[str, str]:
    return auth_headers(MANAGER, key=key, roles=("manager",))


def _sync(client: TestClient, key: str, provider: str = "mock", mode: str = "full") -> dict[str, object]:
    response = client.post(
        "/v1/reservation-syncs",
        json={"provider": provider, "mode": mode},
        headers=_headers(key),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _sync_state(client: TestClient, job_id: str) -> dict[str, object]:
    response = client.get(f"/v1/reservation-syncs/{job_id}", headers=_headers("read-sync"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_seed_creates_fictional_properties_and_does_not_store_cleaners() -> None:
    assert len(FIXTURE_CLEANERS) == 10
    assert count_rows("properties") == PROPERTY_COUNT + 2
    engine = get_engine(__import__("os").environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        names = connection.execute(text("SELECT name FROM properties")).scalars().all()
    assert all(str(name).startswith("Synthetic Property") for name in names)


def test_at01_mock_import_persists_two_reservations_without_tasks(client: TestClient) -> None:
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
    job = _sync(client, "at01")
    state = _sync_state(client, str(job["job_id"]))
    assert state["status"] == "succeeded"
    assert state["rows_applied"] == 2
    assert state["snapshot_complete"] is True
    assert count_rows("reservations") == 2
    assert "reservation_sync.completed" in audit_blob()


def test_at02_duplicate_and_older_revision_do_not_regress(client: TestClient) -> None:
    page = [
        {
            "provider": "mock",
            "external_id": "R-A",
            "property_external_id": "P-A",
            "status": "active",
            "checkin_at": "2026-11-09T16:00:00-05:00",
            "checkout_at": "2026-11-11T10:00:00-05:00",
            "source_revision": "2",
        }
    ]
    set_mock_feed(MockFeed(pages=[page]))
    _sync(client, "at02-a")
    _sync(client, "at02-b", mode="incremental")
    older = dict(page[0])
    older["checkout_at"] = "2026-11-10T10:00:00-05:00"
    older["source_revision"] = "1"
    set_mock_feed(MockFeed(pages=[[older]]))
    _sync(client, "at02-c", mode="full")
    engine = get_engine(__import__("os").environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        checkout = connection.execute(
            text("SELECT checkout_at, source_revision FROM reservations WHERE external_id='R-A'")
        ).one()
    assert str(checkout.source_revision) == "2"
    assert count_rows("reservations") == 1
    assert "reservation.stale_revision_ignored" in audit_blob()


def test_guest_data_and_invalid_timestamp_are_quarantined(client: TestClient) -> None:
    set_mock_feed(
        MockFeed(
            pages=[
                [
                    {
                        "provider": "mock",
                        "external_id": "R-GUEST",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-09T16:00:00-05:00",
                        "checkout_at": "2026-11-10T10:00:00-05:00",
                        "guest_name": "Synthetic Guest",
                    },
                    {
                        "provider": "mock",
                        "external_id": "R-DATE",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-09",
                        "checkout_at": "2026-11-10",
                    },
                    {
                        "provider": "mock",
                        "external_id": "R-MISSING",
                        "property_external_id": "UNMAPPED",
                        "status": "active",
                        "checkin_at": "2026-11-09T16:00:00-05:00",
                        "checkout_at": "2026-11-10T10:00:00-05:00",
                    },
                ]
            ]
        )
    )
    job = _sync(client, "quarantine")
    state = _sync_state(client, str(job["job_id"]))
    assert state["status"] == "succeeded"
    assert count_rows("reservations") == 0
    blob = audit_blob()
    assert "Synthetic Guest" not in blob
    engine = get_engine(__import__("os").environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        codes = connection.execute(text("SELECT code FROM exceptions")).scalars().all()
    assert "GUEST_DATA_REJECTED" in codes
    assert "INVALID_TIMESTAMP" in codes
    assert "UNMAPPED_PROPERTY" in codes


def test_partial_page_does_not_advance_past_the_failure(client: TestClient) -> None:
    set_mock_feed(
        MockFeed(
            pages=[
                [
                    {
                        "provider": "mock",
                        "external_id": "R-PAGE",
                        "property_external_id": "P-A",
                        "status": "active",
                        "checkin_at": "2026-11-09T16:00:00-05:00",
                        "checkout_at": "2026-11-10T10:00:00-05:00",
                        "source_revision": "1",
                    }
                ],
                [],
            ],
            fail_at_page=0,
        )
    )
    job = _sync(client, "partial")
    state = _sync_state(client, str(job["job_id"]))
    assert state["status"] == "failed"
    assert state["cursor_after"] is None
    assert state["snapshot_complete"] is False
    assert count_rows("reservations") == 0
    assert state["publication_blocked"] is True


def test_incomplete_snapshot_does_not_delete_omitted_booking(client: TestClient) -> None:
    first = {
        "provider": "mock",
        "external_id": "R-KEEP",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-09T16:00:00-05:00",
        "checkout_at": "2026-11-10T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[first]], complete=True))
    _sync(client, "keep")
    set_mock_feed(MockFeed(pages=[[]], complete=False))
    job = _sync(client, "omit", mode="full")
    state = _sync_state(client, str(job["job_id"]))
    assert state["snapshot_complete"] is False
    assert state["deletion_inference"] is False
    assert count_rows("reservations") == 1


def test_explicit_cancellation_keeps_the_row(client: TestClient) -> None:
    base = {
        "provider": "mock",
        "external_id": "R-A",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-09T16:00:00-05:00",
        "checkout_at": "2026-11-10T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[base]]))
    _sync(client, "cancel-1")
    canceled = dict(base)
    canceled["status"] = "canceled"
    canceled["source_revision"] = "2"
    set_mock_feed(MockFeed(pages=[[canceled]]))
    _sync(client, "cancel-2", mode="full")
    engine = get_engine(__import__("os").environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        status = connection.execute(text("SELECT status FROM reservations WHERE external_id='R-A'")).scalar_one()
    assert status == "canceled"
    assert count_rows("reservations") == 1


def test_extension_updates_the_same_reservation(client: TestClient) -> None:
    base = {
        "provider": "mock",
        "external_id": "R-A",
        "property_external_id": "P-A",
        "status": "active",
        "checkin_at": "2026-11-09T16:00:00-05:00",
        "checkout_at": "2026-11-10T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[base]]))
    _sync(client, "extend-1")
    extended = dict(base)
    extended["checkout_at"] = "2026-11-11T10:00:00-05:00"
    extended["source_revision"] = "2"
    set_mock_feed(MockFeed(pages=[[extended]]))
    _sync(client, "extend-2", mode="full")
    engine = get_engine(__import__("os").environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id, checkout_at FROM reservations WHERE external_id='R-A'")).one()
    assert count_rows("reservations") == 1
    assert row.checkout_at.day == 11


def test_webhook_rejects_live_and_replays_mock_signature(client: TestClient) -> None:
    disabled = client.post("/v1/webhooks/hospitable", content=b"{}")
    assert disabled.status_code == 403
    assert count_rows("webhook_events") == 0
    bad = client.post("/v1/webhooks/hospitable", content=b"{}", headers={"X-Mock-Signature": "guess"})
    assert bad.status_code == 401
    assert count_rows("webhook_events") == 0
    oversized = client.post(
        "/v1/webhooks/hospitable",
        content=b"x" * (256 * 1024 + 1),
        headers={"X-Mock-Signature": "mock-test-v1"},
    )
    assert oversized.status_code == 413
    payload = (
        b'{"provider":"mock","external_id":"R-HOOK","property_external_id":"P-A","status":"active",'
        b'"checkin_at":"2026-11-09T16:00:00-05:00","checkout_at":"2026-11-10T10:00:00-05:00","source_revision":"1"}'
    )
    headers = {"X-Mock-Signature": "mock-test-v1", "Content-Type": "application/json"}
    first = client.post("/v1/webhooks/hospitable", content=payload, headers=headers)
    second = client.post("/v1/webhooks/hospitable", content=payload, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["event_id"] == second.json()["event_id"]
    assert count_rows("webhook_events") == 1
    assert count_rows("reservations") == 1


def test_csv_preview_commit_is_idempotent_and_blocks_conflicts(client: TestClient) -> None:
    good = (
        "provider,external_id,property_external_id,status,checkin_at,checkout_at,source_revision,source_updated_at\n"
        "mock,R-CSV,P-A,active,2026-11-09T16:00:00-05:00,2026-11-10T10:00:00-05:00,1,2026-11-01T00:00:00Z\n"
    )
    preview = client.post(
        "/v1/reservation-imports/preview",
        headers=_headers("csv-preview"),
        files={"file": ("reservations.csv", io.BytesIO(good.encode()), "text/csv")},
    )
    assert preview.status_code == 201, preview.text
    body = preview.json()
    commit_headers = _headers("csv-commit")
    commit_body = {"expected_digest": body["digest"], "source_order_confirmed": True}
    commit_url = f"/v1/reservation-imports/{body['import_id']}/commit"
    first = client.post(commit_url, json=commit_body, headers=commit_headers)
    second = client.post(commit_url, json=commit_body, headers=commit_headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert count_rows("reservations") == 1
    conflict = (
        "provider,external_id,property_external_id,status,checkin_at,checkout_at,source_revision,source_updated_at\n"
        "mock,R-DUP,P-A,active,2026-11-09T16:00:00-05:00,2026-11-10T10:00:00-05:00,1,\n"
        "mock,R-DUP,P-A,active,2026-11-09T16:00:00-05:00,2026-11-11T10:00:00-05:00,2,\n"
    )
    rejected = client.post(
        "/v1/reservation-imports/preview",
        headers=_headers("csv-bad"),
        files={"file": ("bad.csv", io.BytesIO(conflict.encode()), "text/csv")},
    )
    assert rejected.status_code == 201
    assert rejected.json()["errors"]
    blocked = client.post(
        f"/v1/reservation-imports/{rejected.json()['import_id']}/commit",
        json={"expected_digest": rejected.json()["digest"], "source_order_confirmed": True},
        headers=_headers("csv-block"),
    )
    assert blocked.status_code == 422


def test_feed_host_is_rejected_without_a_live_fetch(client: TestClient) -> None:
    response = client.post(
        "/v1/calendar-feeds/check",
        json={"feed_url": "http://127.0.0.1/secret.ics"},
        headers=_headers("feed"),
    )
    assert response.status_code == 422
    api = client.post(
        "/v1/reservation-syncs",
        json={"provider": "api", "mode": "full"},
        headers=_headers("api-disabled"),
    )
    assert api.status_code == 422


def test_instruction_override_survives_reimport(client: TestClient) -> None:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    from sqlalchemy import text as sql_text

    with factory() as connection:
        property_id = connection.execute(sql_text("SELECT id FROM properties WHERE external_id='P-A'")).scalar_one()
    property_id = str(property_id)
    first = client.post(
        f"/v1/properties/{property_id}/instructions/source",
        json={
            "fields": {"parking": "synthetic north lot", "access_code": "synthetic-code-4455"},
            "source_version": "1",
        },
        headers=_headers("instr-1"),
    )
    assert first.status_code == 201, first.text
    version = first.json()["instruction_version"]
    override = client.post(
        f"/v1/properties/{property_id}/instructions/versions",
        json={
            "expected_version": version,
            "local_overrides": {"parking": "synthetic east lot"},
            "remove_override_fields": [],
            "reason": "Local synthetic override.",
        },
        headers=_headers("instr-2"),
    )
    assert override.status_code == 201, override.text
    reimport = client.post(
        f"/v1/properties/{property_id}/instructions/source",
        json={"fields": {"parking": "synthetic west lot"}, "source_version": "2"},
        headers=_headers("instr-3"),
    )
    assert reimport.status_code == 201, reimport.text
    assert reimport.json()["effective_fields"]["parking"] == "synthetic east lot"
    assert reimport.json()["source_fields"]["parking"] == "synthetic west lot"
    assert "4455" not in audit_blob()
    removed = client.post(
        f"/v1/properties/{property_id}/instructions/versions",
        json={
            "expected_version": reimport.json()["instruction_version"],
            "local_overrides": {},
            "remove_override_fields": ["parking"],
            "reason": "Remove the local override.",
        },
        headers=_headers("instr-4"),
    )
    assert removed.status_code == 201, removed.text
    assert removed.json()["effective_fields"]["parking"] == "synthetic west lot"


def test_pagination_commits_first_page_before_second_page_failure(client: TestClient) -> None:
    row = {
        "provider": "mock",
        "external_id": "R-P1",
        "property_external_id": "P-B",
        "status": "active",
        "checkin_at": "2026-12-01T16:00:00-05:00",
        "checkout_at": "2026-12-02T10:00:00-05:00",
        "source_revision": "1",
    }
    set_mock_feed(MockFeed(pages=[[row], [dict(row, external_id="R-P2")]], fail_at_page=1))
    job = _sync(client, "pages")
    state = _sync_state(client, str(job["job_id"]))
    assert state["status"] == "partial"
    assert state["cursor_after"] == "1"
    assert count_rows("reservations") == 1
    assert uuid.UUID(str(job["job_id"]))
