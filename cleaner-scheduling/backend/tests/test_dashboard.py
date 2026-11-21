from __future__ import annotations

import uuid

from app.clock import SystemClock
from app.config import get_settings
from app.db import session_factory
from app.models import OperationalException
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from tests.support import auth_headers


def _headers(subject: str, roles: tuple[str, ...] = ("manager",)) -> dict[str, str]:
    return auth_headers(subject, key="dash", roles=roles)


def test_dashboard_requires_auth_and_hides_money_from_viewers(client: TestClient) -> None:
    missing = client.get("/v1/dashboard?service_date=2026-11-10")
    assert missing.status_code == 401
    viewer = client.get("/v1/dashboard?service_date=2026-11-10", headers=_headers("synthetic-viewer", ("viewer",)))
    assert viewer.status_code == 200, viewer.text
    body = viewer.json()
    assert "payment_queue_count" not in body
    assert "amount_cents" not in str(body)
    assert body["dispatch_enabled"] is False
    assert "today" in body and "tomorrow" in body
    assert "sync_health" in body and "sms_delivery_counts" in body
    manager = client.get("/v1/dashboard?service_date=2026-11-10", headers=_headers("synthetic-manager"))
    assert manager.status_code == 200
    assert "payment_queue_count" not in manager.json()
    assert "payment_preparation_enabled" not in manager.json()


def test_viewer_cannot_update_exception_and_manager_cannot_waive_feasibility(client: TestClient) -> None:
    exception_id = uuid.uuid4()
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    now = SystemClock().now()
    with factory() as session:
        session.add(
            OperationalException(
                id=exception_id,
                created_at=now,
                updated_at=now,
                version=1,
                code="LOCK_CONFLICT",
                severity="warning",
                dedupe_key=f"dash-lock:{exception_id}",
                status="open",
                task_id=None,
                assignment_id=None,
                sync_run_id=None,
                owner_subject=None,
                details={"reason": "locked"},
                opened_at=now,
                resolved_at=None,
                resolution_reason=None,
            )
        )
        session.commit()
    denied = client.patch(
        f"/v1/exceptions/{exception_id}",
        json={"owner_subject": "synthetic-manager"},
        headers={**_headers("synthetic-viewer", ("viewer",)), "If-Match": '"1"'},
    )
    assert denied.status_code == 403
    owned = client.patch(
        f"/v1/exceptions/{exception_id}",
        json={"owner_subject": "synthetic-manager", "status": "acknowledged"},
        headers={**_headers("synthetic-manager"), "If-Match": '"1"'},
    )
    assert owned.status_code == 200, owned.text
    assert owned.json()["owner_subject"] == "synthetic-manager"
    waived = client.patch(
        f"/v1/exceptions/{exception_id}",
        json={"status": "resolved", "resolution_reason": "ignore the lock"},
        headers={**_headers("synthetic-manager"), "If-Match": '"2"'},
    )
    assert waived.status_code == 422
    assert waived.json()["error"]["code"] == "unresolved_cause"
    stale = client.patch(
        f"/v1/exceptions/{exception_id}",
        json={"owner_subject": "synthetic-owner"},
        headers={**_headers("synthetic-manager"), "If-Match": '"1"'},
    )
    assert stale.status_code == 409
    listed = client.get("/v1/exceptions?status=acknowledged", headers=_headers("synthetic-viewer", ("viewer",)))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["code"] == "LOCK_CONFLICT"
    assert "amount_cents" not in listed.text
