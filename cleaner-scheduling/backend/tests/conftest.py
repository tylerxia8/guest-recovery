"""Test configuration and fixtures.

Database Roles
--------------
The test harness uses two database roles:

- **cleaning_app** (DATABASE_URL): The runtime application role with RLS enforced.
  Sessions use this role and have workspace_id set via GUC for RLS filtering.
  The app guard requires exactly this role name.

- **cleaning_migrator** (MIGRATION_DATABASE_URL): The migration/seeding role with
  SUPERUSER + BYPASSRLS privilege. This role is used by conftest.py:reset_database
  to truncate tables and seed test data visible across RLS contexts.

Cross-workspace tests (e.g. test_cleaner_portal, test_outbox_recovery) use a local
_migrator_session() that connects via MIGRATION_DATABASE_URL to bypass RLS when
seeding data for multiple workspaces.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)
os.environ["APP_ENV"] = "test"
os.environ["OPERATIONAL_TIMEZONE"] = "America/New_York"
os.environ["RESERVATION_PROVIDER"] = "mock"
os.environ["SMS_PROVIDER"] = "mock"
os.environ["ROUTING_PROVIDER"] = "mock"
os.environ["AUTH_PROVIDER"] = "mock"
os.environ["PRODUCTION_INTEGRATIONS_ENABLED"] = "false"
os.environ["PRODUCTION_DISPATCH_ENABLED"] = "false"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://cleaning_app:local-dev-only@127.0.0.1:5432/cleaning"
)
# MIGRATION_DATABASE_URL: role with BYPASSRLS privilege for test seeding.
# See module docstring for details.
os.environ["MIGRATION_DATABASE_URL"] = os.getenv(
    "TEST_MIGRATION_DATABASE_URL", "postgresql+psycopg://cleaning_migrator:local-dev-only@127.0.0.1:5432/cleaning"
)
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6379/1")
os.environ["AUTH_ISSUER"] = "http://localhost/mock-issuer"
os.environ["AUTH_AUDIENCE"] = "cleaning-local"
os.environ["AUTH_MOCK_SECRET"] = "test-only-mock-secret-not-a-credential"
os.environ["POLICY_APPROVAL_REQUIRED"] = "true"
os.environ["CORS_ORIGINS"] = "http://127.0.0.1:3000"
os.environ["HOSPITABLE_CREDENTIAL_SECRET_REF"] = ""
os.environ["HOSPITABLE_WEBHOOK_SECRET_REF"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_MESSAGING_SERVICE_SID"] = ""
os.environ["GOOGLE_MAPS_API_KEY"] = ""

from datetime import UTC, datetime

import pytest
import redis
from app.adapters.reservations.mock import reset_mock_feed
from app.adapters.routing.mock import clear_travel_overrides
from app.adapters.sms.mock import reset_mock_sms_outcome
from app.api.app_factory import create_app
from app.clock import FrozenClock
from app.config import Settings, clear_settings_cache, get_settings
from app.db import dispose_engines, get_engine, session_factory
from app.seed import seed_operators, seed_properties
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

TEST_CLOCK = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=UTC))

TABLES = (
    "workspace_connections",
    "manager_sessions",
    "manager_accounts",
    "email_deliveries",
    "cleaning_offers",
    "payment_line_items",
    "payment_batches",
    "acknowledgments",
    "sms_messages",
    "cleaner_access_links",
    "assignments",
    "cleaner_schedule_versions",
    "schedule_runs",
    "schedule_days",
    "routing_snapshots",
    "cleaner_daily_availability",
    "cleaner_weekly_hours",
    "cleaner_property_eligibility",
    "cleaner_rates",
    "cleaners",
    "cleaning_tasks",
    "reservation_import_previews",
    "reservations",
    "property_instructions",
    "exceptions",
    "reservation_sync_runs",
    "properties",
    "idempotency_records",
    "outbox_events",
    "webhook_events",
    "audit_logs",
    "policy_versions",
    "operator_profiles",
)


def run_alembic(*args: str) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=os.environ.copy(),
        timeout=60,
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    run_alembic("upgrade", "head")
    yield
    dispose_engines()


@pytest.fixture(autouse=True)
def reset_database() -> Iterator[None]:
    clear_settings_cache()
    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)}"))
        connection.execute(text("DELETE FROM workspaces WHERE id <> :legacy"), {"legacy": LEGACY_WORKSPACE_ID})
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as session:
        seed_operators(session, TEST_CLOCK)
        seed_properties(session, TEST_CLOCK)
        session.commit()
    reset_mock_feed()
    clear_travel_overrides()
    reset_mock_sms_outcome()
    redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    clear_settings_cache()
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app(get_settings(), TEST_CLOCK)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def session() -> Iterator[Session]:
    """Provide a database session for tests."""
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as sess:
        yield sess


@pytest.fixture
def settings() -> Settings:
    """Provide settings for tests."""
    return get_settings()


@pytest.fixture
def clock() -> FrozenClock:
    """Provide a frozen clock for tests."""
    return TEST_CLOCK
