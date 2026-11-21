from __future__ import annotations

from app.models import Assignment, CleanerRate, PaymentBatch
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.completed_fixture import complete_fixture, db
from tests.support import auth_headers


def test_rate_free_cleaner_can_be_scheduled_and_complete_work(client: TestClient) -> None:
    cleaner = complete_fixture(client, two=True)
    with db() as session:
        assert session.scalars(select(CleanerRate)).all() == []
        assert session.scalars(select(PaymentBatch)).all() == []
        assignments = session.scalars(select(Assignment)).all()
        assert len(assignments) == 2
        assert all(row.status == "completed" for row in assignments)
        assert all(row.rate_id is None and row.amount_cents is None for row in assignments)
        assert all(row.compensation_snapshot == {} for row in assignments)
    headers = auth_headers("synthetic-owner", key="removed-finance")
    for method, path in [
        ("GET", "/v1/payment-batches"),
        ("POST", "/v1/payment-batches/prepare"),
        ("GET", f"/v1/cleaners/{cleaner}/rates"),
        ("POST", f"/v1/cleaners/{cleaner}/rates"),
        ("POST", f"/v1/cleaners/{cleaner}/rates/{cleaner}/replace"),
    ]:
        assert client.request(method, path, headers=headers, json={}).status_code == 404
    setup = client.get("/v1/setup", headers=headers)
    assert setup.status_code == 200
    assert "cleaners_without_any_current_rate" not in setup.json()
