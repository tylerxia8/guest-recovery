"""Tests for app role security validation (db.py:validate_app_role).

These tests use real Postgres (CI harness) to verify that the application
refuses to start with privileged database credentials.

Security model:
- Application must connect as exactly cleaning_app
- cleaning_app must not be SUPERUSER, BYPASSRLS, or CREATEROLE
- cleaning_app must not own any RLS-protected tables
- FORCE ROW LEVEL SECURITY must be enabled on all tenant tables

Some tests create temporary roles and require the migrator to have CREATEROLE
or SUPERUSER. These are skipped locally when the migrator lacks this privilege,
but CI always runs them (the CI migrator is superuser).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from app.config import get_settings
from app.db import get_engine, validate_app_role
from app.domain.errors import ConfigError
from sqlalchemy import create_engine, text


def _in_ci() -> bool:
    """Check if running in CI environment."""
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def _migrator_can_create_roles() -> bool:
    """Check if the migrator role can create roles (SUPERUSER or CREATEROLE)."""
    migrator_url = os.environ["MIGRATION_DATABASE_URL"]
    engine = create_engine(migrator_url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).fetchone()
        can_create = bool(row and row[0])
    engine.dispose()
    return can_create


class TestValidateAppRoleRefusesNonCleaningApp:
    """Test that validate_app_role refuses any role other than cleaning_app."""

    def test_migrator_refused(self) -> None:
        """Connecting as migrator (not cleaning_app) must fail.

        Mutation: Remove role_name != 'cleaning_app' check at db.py.
        Fails: assert exc_info.value.code == "app_role_not_cleaning_app"
        """
        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        engine = create_engine(migrator_url)

        with pytest.raises(ConfigError) as exc_info:
            validate_app_role(engine)

        assert exc_info.value.code == "app_role_not_cleaning_app"
        engine.dispose()


class TestValidateAppRoleRefusesBypassRLS:
    """Test that validate_app_role refuses BYPASSRLS on cleaning_app."""

    def test_bypassrls_refused_if_cleaning_app_has_it(self) -> None:
        """If cleaning_app somehow got BYPASSRLS, startup must refuse.

        This test temporarily grants BYPASSRLS to cleaning_app, verifies
        the guard catches it, then revokes. This requires superuser migrator.

        Mutation: Remove rolbypassrls check at db.py validate_app_role.
        Fails: assert exc_info.value.code == "app_role_bypassrls"
        """
        if _in_ci():
            assert _migrator_can_create_roles(), "CI migrator must have SUPERUSER"
        elif not _migrator_can_create_roles():
            pytest.skip("migrator lacks SUPERUSER")

        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        migrator_engine = create_engine(migrator_url)

        with migrator_engine.begin() as conn:
            conn.execute(text("ALTER ROLE cleaning_app BYPASSRLS"))

        try:
            settings = get_settings()
            engine = get_engine(settings.database_url)
            with pytest.raises(ConfigError) as exc_info:
                validate_app_role(engine)

            assert exc_info.value.code == "app_role_bypassrls"
        finally:
            with migrator_engine.begin() as conn:
                conn.execute(text("ALTER ROLE cleaning_app NOBYPASSRLS"))
            migrator_engine.dispose()


class TestValidateAppRoleRefusesCreaterole:
    """Test that validate_app_role refuses CREATEROLE on cleaning_app."""

    def test_createrole_refused_if_cleaning_app_has_it(self) -> None:
        """If cleaning_app somehow got CREATEROLE, startup must refuse.

        This test temporarily grants CREATEROLE to cleaning_app, verifies
        the guard catches it, then revokes. This requires superuser migrator.

        Mutation: Remove rolcreaterole check at db.py validate_app_role.
        Fails: assert exc_info.value.code == "app_role_createrole"
        """
        if _in_ci():
            assert _migrator_can_create_roles(), "CI migrator must have SUPERUSER"
        elif not _migrator_can_create_roles():
            pytest.skip("migrator lacks SUPERUSER")

        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        migrator_engine = create_engine(migrator_url)

        with migrator_engine.begin() as conn:
            conn.execute(text("ALTER ROLE cleaning_app CREATEROLE"))

        try:
            settings = get_settings()
            engine = get_engine(settings.database_url)
            with pytest.raises(ConfigError) as exc_info:
                validate_app_role(engine)

            assert exc_info.value.code == "app_role_createrole"
        finally:
            with migrator_engine.begin() as conn:
                conn.execute(text("ALTER ROLE cleaning_app NOCREATEROLE"))
            migrator_engine.dispose()


class TestValidateAppRoleRefusesTableOwner:
    """Test that validate_app_role refuses if cleaning_app owns an RLS table."""

    def test_table_owner_refused(self) -> None:
        """Startup refuses if cleaning_app owns an RLS table.

        Creates a table owned by cleaning_app with workspace_id column.
        Mutation: Remove ownership check at db.py validate_app_role.
        Fails: assert exc_info.value.code == "app_role_owns_table"
        """
        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        migrator_engine = create_engine(migrator_url)

        table_name = "test_owned_table"

        with migrator_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            conn.execute(text(f"CREATE TABLE {table_name} (id UUID PRIMARY KEY, workspace_id UUID NOT NULL)"))
            conn.execute(text(f"ALTER TABLE {table_name} OWNER TO cleaning_app"))
            conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))

        try:
            settings = get_settings()
            engine = get_engine(settings.database_url)
            with pytest.raises(ConfigError) as exc_info:
                validate_app_role(engine)

            assert exc_info.value.code == "app_role_owns_table"
        finally:
            with migrator_engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            migrator_engine.dispose()


class TestValidateAppRoleRefusesMissingForceRLS:
    """Test that validate_app_role refuses tables without FORCE RLS."""

    def test_missing_force_rls_refused(self) -> None:
        """Startup refuses when a tenant table lacks FORCE RLS.

        Creates a scratch table with ENABLE but not FORCE RLS.
        Mutation: Remove force_rls_missing check at db.py validate_app_role.
        Fails: assert exc_info.value.code == "force_rls_missing"
        """
        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        migrator_engine = create_engine(migrator_url)

        table_name = "test_no_force_rls"

        with migrator_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            conn.execute(text(f"CREATE TABLE {table_name} (id UUID PRIMARY KEY, workspace_id UUID NOT NULL)"))
            conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))

        try:
            settings = get_settings()
            engine = get_engine(settings.database_url)
            with pytest.raises(ConfigError) as exc_info:
                validate_app_role(engine)

            assert exc_info.value.code == "force_rls_missing"
        finally:
            with migrator_engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            migrator_engine.dispose()


class TestValidateAppRoleAcceptsCleaningApp:
    """Test that validate_app_role accepts the proper cleaning_app role."""

    def test_cleaning_app_accepted(self) -> None:
        """Startup accepts cleaning_app with proper privileges.

        Mutation: Always raise ConfigError in validate_app_role.
        Fails: Should not raise.
        """
        settings = get_settings()
        engine = get_engine(settings.database_url)
        validate_app_role(engine)


class TestWorkerInitRefusesPrivileged:
    """Test that celery worker_init refuses privileged roles."""

    def test_worker_init_refuses_non_cleaning_app(self) -> None:
        """Starting celery worker with non-cleaning_app URL must fail.

        Runs celery worker in subprocess with migrator URL.
        Mutation: Remove validate_app_role call from worker_init handler.
        Fails: Worker would start (exit 0).
        """
        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")

        env = os.environ.copy()
        env["DATABASE_URL"] = migrator_url
        env["REDIS_URL"] = redis_url
        env["AUTH_ISSUER"] = "http://localhost/mock"
        env["AUTH_AUDIENCE"] = "test"
        env["AUTH_MOCK_SECRET"] = "test-only-mock-secret-not-a-credential"
        env["OPERATIONAL_TIMEZONE"] = "America/New_York"
        env["APP_ENV"] = "test"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.workers.celery_app:celery_app",
                "worker",
                "--loglevel=error",
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0, (
            f"Expected nonzero exit, got {result.returncode}. stderr: {result.stderr.decode()}"
        )
        assert b"app_role_not_cleaning_app" in result.stderr, (
            f"Expected app_role_not_cleaning_app in stderr: {result.stderr.decode()}"
        )

    def test_worker_init_uses_throwaway_engine(self) -> None:
        """worker_init uses a throwaway engine, not the cached one.

        The cached engine (db._engines) should not be populated by worker_init
        to avoid pre-fork connection issues.

        Mutation: Use get_engine() instead of throwaway NullPool engine.
        Fails: Cached engine would exist after validation.
        """
        from app import db

        url = os.environ["DATABASE_URL"]
        if url in db._engines:
            db._engines[url].dispose()
            del db._engines[url]

        from app.workers.celery_app import validate_database_role_on_worker_init

        validate_database_role_on_worker_init()

        assert url not in db._engines, "worker_init should not populate cached engine"

    def test_worker_init_refuses_bad_password(self) -> None:
        """Starting celery worker with bad password must fail.

        Runs celery worker in subprocess with invalid password.
        Mutation: Catch only ConfigError instead of Exception.
        Fails: Worker would start (exit 0) with OperationalError swallowed.
        """
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")

        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql+psycopg://cleaning_app:wrong-password@127.0.0.1:5432/cleaning"
        env["REDIS_URL"] = redis_url
        env["AUTH_ISSUER"] = "http://localhost/mock"
        env["AUTH_AUDIENCE"] = "test"
        env["AUTH_MOCK_SECRET"] = "test-only-mock-secret-not-a-credential"
        env["OPERATIONAL_TIMEZONE"] = "America/New_York"
        env["APP_ENV"] = "test"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.workers.celery_app:celery_app",
                "worker",
                "--loglevel=error",
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0, (
            f"Expected nonzero exit with bad password, got {result.returncode}. stderr: {result.stderr.decode()}"
        )


class TestCleaningAppGrants:
    """Test that cleaning_app has proper access restrictions."""

    def test_cleaning_app_can_select_alembic_version(self) -> None:
        """cleaning_app can SELECT alembic_version.

        Mutation: Revoke SELECT on alembic_version from cleaning_app.
        Fails: Permission denied on alembic_version.
        """
        settings = get_settings()
        engine = get_engine(settings.database_url)

        with engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = result.fetchall()
            assert len(rows) >= 1

    def test_cleaning_app_cannot_delete_audit_logs(self) -> None:
        """cleaning_app cannot DELETE from audit_logs (append-only).

        Mutation: Grant DELETE on audit_logs to cleaning_app.
        Fails: DELETE would succeed.
        """
        settings = get_settings()
        engine = get_engine(settings.database_url)

        from sqlalchemy.exc import ProgrammingError

        with pytest.raises(ProgrammingError) as exc_info:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM audit_logs WHERE id = '00000000-0000-0000-0000-000000000000'"))

        assert "permission denied" in str(exc_info.value).lower()


class TestStartupLogging:
    """Test that validate_app_role logs role info at startup."""

    def test_logs_role_info_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Startup logs current_user, rolsuper, rolbypassrls at WARNING level.

        Mutation: Remove logger.warning call at db.py validate_app_role.
        Fails: assert "db.validate_app_role" in caplog.text
        """
        import logging

        settings = get_settings()
        engine = create_engine(settings.database_url)

        with caplog.at_level(logging.WARNING, logger="app.db"):
            validate_app_role(engine)

        assert "db.validate_app_role" in caplog.text
        assert "cleaning_app" in caplog.text
        assert "rolsuper=False" in caplog.text
        assert "rolbypassrls=False" in caplog.text
        assert "rolinherit=" in caplog.text
        engine.dispose()


class TestCreateAppRefusesPrivileged:
    """Test that create_app refuses non-cleaning_app roles."""

    def test_create_app_refuses_non_cleaning_app(self) -> None:
        """create_app with migrator URL must fail.

        Mutation: Remove validate_app_role from app_factory.py.
        Fails: App would start.
        """
        from datetime import UTC, datetime

        from app.api.app_factory import create_app
        from app.clock import FrozenClock
        from app.config import Settings

        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        settings = Settings(
            database_url=migrator_url,
            redis_url="redis://localhost:6379/1",
            workspace_signup_enabled=False,
            operational_timezone="America/New_York",
        )

        with pytest.raises(ConfigError) as exc_info:
            create_app(settings, FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)))

        assert exc_info.value.code == "app_role_not_cleaning_app"


class TestValidateAppRoleRefusesSuperuser:
    """Test that validate_app_role refuses SUPERUSER on cleaning_app."""

    def test_superuser_refused_if_cleaning_app_has_it(self) -> None:
        """If cleaning_app somehow got SUPERUSER, startup must refuse.

        This test temporarily grants SUPERUSER to cleaning_app, verifies
        the guard catches it, then revokes. This requires superuser migrator.

        Mutation: Remove rolsuper check at db.py validate_app_role.
        Fails: assert exc_info.value.code == "app_role_superuser"
        """
        if _in_ci():
            assert _migrator_can_create_roles(), "CI migrator must have SUPERUSER"
        elif not _migrator_can_create_roles():
            pytest.skip("migrator lacks SUPERUSER")

        migrator_url = os.environ["MIGRATION_DATABASE_URL"]
        migrator_engine = create_engine(migrator_url)

        with migrator_engine.begin() as conn:
            conn.execute(text("ALTER ROLE cleaning_app SUPERUSER"))

        try:
            settings = get_settings()
            engine = get_engine(settings.database_url)
            with pytest.raises(ConfigError) as exc_info:
                validate_app_role(engine)

            assert exc_info.value.code == "app_role_superuser"
        finally:
            with migrator_engine.begin() as conn:
                conn.execute(text("ALTER ROLE cleaning_app NOSUPERUSER"))
            migrator_engine.dispose()
