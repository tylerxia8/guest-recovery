from __future__ import annotations

import os
from datetime import UTC, datetime

import redis
from app.api.app_factory import create_app
from app.clock import FrozenClock, SystemClock
from app.config import get_settings
from app.db import session_factory
from app.models import OperatorProfile
from app.seed import SYNTHETIC_OPERATORS, seed_operators
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support import auth_headers, count_rows, valid_settings


def test_seed_is_idempotent_and_fictional() -> None:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as session:
        inserted = seed_operators(session, SystemClock())
        session.commit()
        total = session.scalar(select(func.count()).select_from(OperatorProfile))
    assert inserted == 0
    assert total == len(SYNTHETIC_OPERATORS)
    for subject, display_name, _roles, _active in SYNTHETIC_OPERATORS:
        assert subject.startswith("synthetic-")
        assert "synthetic" in display_name.lower()
        assert "+" not in display_name


def test_write_rate_limit_and_redis_outage() -> None:
    settings = get_settings().model_copy(update={"write_rate_limit_per_minute": 2})
    clock = FrozenClock(datetime(2026, 11, 10, 18, 0, tzinfo=UTC))
    limited = TestClient(create_app(settings, clock))
    body = {"settings": valid_settings(), "reason": "Record proposed defaults."}
    statuses = [
        limited.post(
            "/v1/policy-versions",
            json=body,
            headers=auth_headers("synthetic-owner", key=f"rate-{index}"),
        ).status_code
        for index in range(3)
    ]
    assert statuses == [201, 201, 429]
    assert count_rows("policy_versions") == 2
    offline = get_settings().model_copy(update={"redis_url": "redis://127.0.0.1:6399/0"})
    client = TestClient(create_app(offline, clock))
    response = client.post(
        "/v1/policy-versions",
        json=body,
        headers=auth_headers("synthetic-owner", key="redis-down"),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    redis.Redis.from_url(os.environ["REDIS_URL"]).ping()
