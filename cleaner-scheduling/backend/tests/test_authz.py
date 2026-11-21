from __future__ import annotations

from fastapi.testclient import TestClient
from tests.support import audit_blob, auth_headers, count_rows, make_token, valid_settings


def _post(client: TestClient, headers: dict[str, str] | None = None, body: dict[str, object] | None = None):
    payload = body or {"settings": valid_settings(), "reason": "Record proposed defaults."}
    return client.post("/v1/policy-versions", json=payload, headers=headers or {})


def test_missing_token_is_401_without_business_writes(client: TestClient) -> None:
    response = _post(client)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"
    assert count_rows("policy_versions") == 0
    assert count_rows("outbox_events") == 0
    assert count_rows("idempotency_records") == 0
    assert "security.access_denied" in audit_blob()


def test_invalid_and_expired_tokens_are_401(client: TestClient) -> None:
    invalid = _post(client, {"Authorization": "Bearer not-a-token", "Idempotency-Key": "k"})
    assert invalid.status_code == 401
    expired = _post(
        client,
        auth_headers("synthetic-owner", expired=True),
    )
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "token_expired"
    blob = audit_blob()
    assert "not-a-token" not in blob
    assert make_token("synthetic-owner", expired=True) not in blob


def test_token_role_claims_do_not_grant_access(client: TestClient) -> None:
    response = _post(client, auth_headers("synthetic-viewer", roles=("owner", "manager")))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"
    assert count_rows("policy_versions") == 0
    assert count_rows("outbox_events") == 0


def test_database_role_allows_owner_even_if_token_claims_viewer(client: TestClient) -> None:
    response = _post(client, auth_headers("synthetic-owner", roles=("viewer",)))
    assert response.status_code == 201
    assert response.json()["approved_at"] is None


def test_manager_scheduler_and_deactivated_are_denied(client: TestClient) -> None:
    phone = "+17045550199"
    body = {
        "settings": valid_settings(),
        "reason": f"call {phone} about access code=9999",
    }
    for subject in ("synthetic-manager", "synthetic-scheduler", "synthetic-deactivated"):
        response = _post(client, auth_headers(subject, key=f"k-{subject}"), body)
        assert response.status_code == 403
    assert count_rows("policy_versions") == 0
    assert count_rows("outbox_events") == 0
    blob = audit_blob()
    assert phone not in blob
    assert "9999" not in blob
    assert "inactive_operator" in blob
    assert "insufficient_role" in blob


def test_authorized_create_audits_without_outbox(client: TestClient) -> None:
    response = _post(client, auth_headers("synthetic-owner"))
    assert response.status_code == 201
    assert count_rows("outbox_events") == 0
    assert count_rows("webhook_events") == 0
    assert "policy_version.created" in audit_blob()
