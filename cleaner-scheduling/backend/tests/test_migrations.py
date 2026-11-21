from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from app.config import get_settings
from app.db import dispose_engines, get_engine, session_factory
from app.workspaces import LEGACY_WORKSPACE_ID
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from tests.conftest import run_alembic

FOUNDATION_TABLES = {
    "operator_profiles",
    "policy_versions",
    "audit_logs",
    "webhook_events",
    "outbox_events",
    "idempotency_records",
}


def test_foundation_tables_exist_and_domain_tables_do_not() -> None:
    inspector = inspect(get_engine(os.environ["MIGRATION_DATABASE_URL"]))
    names = set(inspector.get_table_names())
    assert FOUNDATION_TABLES <= names
    assert "reservations" in names
    assert "cleaning_tasks" in names
    assert "schedule_runs" in names
    assert "assignments" in names
    assert "sms_messages" in names
    assert "acknowledgments" in names
    assert "payment_batches" in names
    assert "payment_line_items" in names


def test_empty_database_migration_round_trip() -> None:
    dispose_engines()
    try:
        run_alembic("downgrade", "base")
        run_alembic("upgrade", "head")
        inspector = inspect(get_engine(os.environ["MIGRATION_DATABASE_URL"]))
        assert FOUNDATION_TABLES <= set(inspector.get_table_names())
        run_alembic("downgrade", "base")
        inspector = inspect(get_engine(os.environ["MIGRATION_DATABASE_URL"]))
        assert FOUNDATION_TABLES.isdisjoint(inspector.get_table_names())
    finally:
        run_alembic("upgrade", "head")
        dispose_engines()


def test_application_role_cannot_update_or_delete_audits() -> None:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    audit_id = uuid.uuid4()
    now = datetime.now(UTC)
    with factory.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_logs (id, created_at, actor_type, actor_subject, action, "
                "entity_type, correlation_id, occurred_at, redacted_changes) "
                "VALUES (:id, :now, 'system', 'test', 'security.access_denied', 'http_route', "
                ":correlation, :now, '{}'::jsonb)"
            ),
            {"id": audit_id, "now": now, "correlation": uuid.uuid4()},
        )
    with pytest.raises(DBAPIError):
        with factory.begin() as connection:
            connection.execute(
                text("UPDATE audit_logs SET action = 'tamper' WHERE id = :id"),
                {"id": audit_id},
            )
    with pytest.raises(DBAPIError):
        with factory.begin() as connection:
            connection.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": audit_id})
