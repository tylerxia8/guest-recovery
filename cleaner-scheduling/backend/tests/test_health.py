from __future__ import annotations

from app.api.routes.health import schema_matches
from fastapi.testclient import TestClient


def test_liveness_has_no_provider_dependency(client: TestClient) -> None:
    response = client.get("/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    assert "authorization" not in response.text.lower()


def test_readiness_reports_schema_revision(client: TestClient) -> None:
    response = client.get("/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "schema_revision": "0031_action_inbox_claims"}


def test_schema_match_helper() -> None:
    assert schema_matches(["0031_action_inbox_claims"]) is True
    assert schema_matches([]) is False
    assert schema_matches(["0004_scheduling"]) is False
