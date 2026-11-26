import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.routes.ingestion import (
    OperationsTouchupWrite,
    operations_cleaning_status,
    operations_cleaning_touchup,
    operations_stays,
)
from app.config import get_settings
from app.db import get_engine, session_factory
from app.domain.errors import AppError
from app.services import guest_recovery
from sqlalchemy import text


def check_cleaning_handoff_isolation(settings, migrator):
    now = datetime.now(UTC)
    workspace_a, workspace_b = uuid4(), uuid4()
    booking, property_source, request = str(uuid4()), str(uuid4()), str(uuid4())
    with migrator.begin() as connection:
        for index, workspace in enumerate((workspace_a, workspace_b)):
            prop = uuid4()
            connection.execute(
                text("INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, 'Fixture Workspace')"),
                {"id": workspace, "now": now},
            )
            connection.execute(
                text("""
                    INSERT INTO properties
                    (id, workspace_id, created_at, updated_at, version, provider, external_id, name, bedrooms,
                     location_ciphertext, geocode_status, active)
                    VALUES (:id, :workspace, :now, :now, 1, 'hospitable', :source, :name, 2,
                            :location, 'unavailable', true)
                """),
                {
                    "id": prop,
                    "workspace": workspace,
                    "now": now,
                    "source": property_source,
                    "location": b"fixture",
                    "name": f"Fixture House {index}",
                },
            )
            connection.execute(
                text("""
                    INSERT INTO reservations
                    (id, workspace_id, created_at, updated_at, version, property_id, provider, external_id,
                     status, checkin_at, checkout_at, observed_at, timestamp_provenance, payload_digest)
                    VALUES (:id, :workspace, :now, :now, 1, :property, 'hospitable', :booking,
                            'active', :checkin, :checkout, :now, 'source', :digest)
                """),
                {
                    "id": uuid4(),
                    "workspace": workspace,
                    "now": now,
                    "property": prop,
                    "booking": booking,
                    "checkin": now - timedelta(hours=1),
                    "checkout": now + timedelta(days=1),
                    "digest": "a" * 64,
                },
            )
    body = {
        "external_request_id": request,
        "property_external_id": property_source,
        "reservation_external_id": booking,
        "release_at": (now + timedelta(hours=1)).isoformat(),
        "deadline_at": (now + timedelta(hours=3)).isoformat(),
        "scope": "Replace the bedding",
        "duration_minutes": 30,
        "guest_access_confirmed": True,
    }
    root = "/v1/cleaner-returns"
    read_path = f"{root}/{request}"
    first = session_factory(settings, workspace_id=workspace_a)
    second = session_factory(settings, workspace_id=workspace_b)
    read_settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)
    for index, factory in enumerate((first, second)):
        with factory() as session:
            stays = json.loads(operations_stays(None, session, read_settings, SimpleNamespace(now=lambda: now)).body)
            assert [stay["property_name"] for stay in stays["stays"]] == [f"Fixture House {index}"]
    touchup_settings = SimpleNamespace(
        workspace_signup_enabled=True,
        operations_login_enabled=True,
        operations_touchup_enabled=True,
        local_scheduling_enabled=True,
    )
    with first() as session:
        task_a = json.loads(
            operations_cleaning_touchup(
                OperationsTouchupWrite(**body),
                None,
                session,
                touchup_settings,
            ).body
        )
        assert guest_recovery.handle(session, "POST", root, body) == task_a
    with second.begin() as session:
        with pytest.raises(LookupError):
            guest_recovery.handle(session, "GET", read_path, None)
        with pytest.raises(AppError) as missing:
            operations_cleaning_status(UUID(request), property_source, booking, None, session, touchup_settings)
        assert missing.value.status_code == 404
        task_b = guest_recovery.handle(session, "POST", root, body)
    assert task_a["task_id"] != task_b["task_id"]
    with first.begin() as session:
        assert guest_recovery.handle(session, "GET", read_path, None) == task_a
        result = operations_cleaning_status(UUID(request), property_source, booking, None, session, touchup_settings)
        assert json.loads(result.body) == task_a
    with second.begin() as session:
        assert guest_recovery.handle(session, "GET", read_path, None) == task_b
        with pytest.raises(AppError):
            operations_cleaning_status(
                UUID(request), property_source, "unrelated-stay", None, session, touchup_settings
            )

    legacy_request = uuid4()
    with migrator.begin() as connection:
        connection.execute(
            text("UPDATE cleaning_tasks SET external_return_request_id = :request WHERE id = :task"),
            {"request": legacy_request, "task": task_a["task_id"]},
        )
    with first.begin() as session:
        result = operations_cleaning_status(legacy_request, property_source, booking, None, session, touchup_settings)
        assert json.loads(result.body) == task_a
    with second.begin() as session, pytest.raises(AppError) as denied:
        operations_cleaning_status(legacy_request, property_source, booking, None, session, touchup_settings)
    assert denied.value.status_code == 404


def test_cleaning_handoff_isolation():
    check_cleaning_handoff_isolation(get_settings(), get_engine(os.environ["MIGRATION_DATABASE_URL"]))
