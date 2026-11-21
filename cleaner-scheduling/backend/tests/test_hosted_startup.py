"""Tests for hosted_startup.py and config.py validate_startup.

These tests verify:
1. hosted_workspace_isolation_required conditions (config.py)
2. read_only_isolation_required conditions (config.py)
3. hosted_startup.py pops MIGRATION_DATABASE_URL after alembic, before execve
"""

from __future__ import annotations

import os
import sys
from unittest import mock

import pytest
from app.config import Settings, clear_settings_cache, validate_startup
from app.domain.errors import ConfigError


class TestHostedValidateStartupWithoutMigrationUrl:
    """Test that hosted mode validate_startup passes without MIGRATION_DATABASE_URL."""

    def test_hosted_validate_startup_without_migration_database_url(self) -> None:
        """Hosted mode validate_startup passes without MIGRATION_DATABASE_URL.

        After alembic runs, hosted_startup.py:80 pops MIGRATION_DATABASE_URL.
        serve() then calls get_settings() and validate_startup() must pass.

        Mutation: Add migration_database_url back to _non_empty validator at config.py:101.
        Fails: ValidationError("must not be empty") when Settings is constructed.
        """
        hosted_env = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app:pass@db.railway.internal:5432/railway",
            "REDIS_URL": "redis://default:pass@redis.railway.internal:6379/0",
            "AUTH_ISSUER": "https://auth.example.com/",
            "AUTH_AUDIENCE": "api://cleaning-app",
            "HOSPITABLE_READ_ONLY": "true",
            "WORKSPACE_SIGNUP_ENABLED": "true",
            "FIELD_ENCRYPTION_KEY_FILE": "/data/private/field-key",
            "CLEANER_PORTAL_BASE_URL": "https://app.example.com",
            "API_ALLOWED_HOSTS": "api.example.com",
            "WEB_PROXY_SECRET": "a" * 43,
            "AUTH_PROVIDER": "workspace",
            "RESERVATION_PROVIDER": "hospitable",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "MIGRATION_DATABASE_URL": "",
            "AUTH_MOCK_SECRET": "",
            "HOSPITABLE_CREDENTIAL_SECRET_REF": "",
            "HOSPITABLE_WEBHOOK_SECRET_REF": "",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
            "TWILIO_MESSAGING_SERVICE_SID": "",
            "GOOGLE_MAPS_API_KEY": "",
        }

        clear_settings_cache()
        with mock.patch.dict(os.environ, hosted_env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.hosted_workspace is True, "Should be hosted_workspace mode"
            assert settings.migration_database_url == "", (
                "migration_database_url should be empty string when env var is not set"
            )
            with mock.patch.object(
                type(settings), "field_encryption_secret", property(lambda self: "mock-encryption-secret")
            ):
                validate_startup(settings)


class TestLocalReadOnlyWithoutMigrationUrl:
    """Test that local read-only mode without MIGRATION_DATABASE_URL raises."""

    def test_local_read_only_without_migration_url_raises(self) -> None:
        """Local read-only mode without MIGRATION_DATABASE_URL raises read_only_isolation_required.

        Mutation: Remove 'migration is None' check at config.py:328.
        Fails: Would not raise ConfigError.
        """
        local_env = {
            "APP_ENV": "development",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app:pass@localhost:5432/cleaning_hospitable",
            "REDIS_URL": "redis://localhost:6379/6",
            "AUTH_ISSUER": "https://auth.example.com/",
            "AUTH_AUDIENCE": "api://cleaning-app",
            "HOSPITABLE_READ_ONLY": "true",
            "HOSPITABLE_TOKEN_FILE": "/path/to/token",
            "FIELD_ENCRYPTION_KEY_FILE": "/path/to/key",
            "AUTH_MOCK_SECRET": "a" * 32,
            "AUTH_PROVIDER": "mock",
            "RESERVATION_PROVIDER": "hospitable",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "MIGRATION_DATABASE_URL": "",
            "HOSPITABLE_CREDENTIAL_SECRET_REF": "",
            "HOSPITABLE_WEBHOOK_SECRET_REF": "",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
            "TWILIO_MESSAGING_SERVICE_SID": "",
            "GOOGLE_MAPS_API_KEY": "",
        }

        clear_settings_cache()
        with mock.patch.dict(os.environ, local_env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.hospitable_read_only is True
            assert settings.hosted_workspace is False
            assert settings.migration_database_url == ""
            with pytest.raises(ConfigError) as exc_info:
                validate_startup(settings)
            assert exc_info.value.code == "read_only_isolation_required"


class TestHostedWorkspaceIsolationRequired:
    """Test hosted_workspace_isolation_required exact error codes."""

    def test_hosted_refuses_wrong_username(self) -> None:
        """Hosted mode refuses when DATABASE_URL username is not cleaning_app.

        Mutation: Remove database.username != "cleaning_app" check at config.py.
        Fails: Would not raise ConfigError.
        """
        hosted_env = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://wrong_user:pass@db.railway.internal:5432/railway",
            "REDIS_URL": "redis://default:pass@redis.railway.internal:6379/0",
            "AUTH_ISSUER": "https://auth.example.com/",
            "AUTH_AUDIENCE": "api://cleaning-app",
            "HOSPITABLE_READ_ONLY": "true",
            "WORKSPACE_SIGNUP_ENABLED": "true",
            "FIELD_ENCRYPTION_KEY_FILE": "/data/private/field-key",
            "CLEANER_PORTAL_BASE_URL": "https://app.example.com",
            "API_ALLOWED_HOSTS": "api.example.com",
            "WEB_PROXY_SECRET": "a" * 43,
            "AUTH_PROVIDER": "workspace",
            "RESERVATION_PROVIDER": "hospitable",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "MIGRATION_DATABASE_URL": "",
            "HOSPITABLE_CREDENTIAL_SECRET_REF": "",
            "HOSPITABLE_WEBHOOK_SECRET_REF": "",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
            "TWILIO_MESSAGING_SERVICE_SID": "",
            "GOOGLE_MAPS_API_KEY": "",
        }

        clear_settings_cache()
        with mock.patch.dict(os.environ, hosted_env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.hosted_workspace is True
            with pytest.raises(ConfigError) as exc_info:
                validate_startup(settings)
            assert exc_info.value.code == "hosted_workspace_isolation_required"

    def test_hosted_refuses_non_railway_internal_db_host(self) -> None:
        """Hosted mode refuses when DATABASE_URL host doesn't end in .railway.internal.

        Mutation: Remove database.host.endswith(".railway.internal") check at config.py.
        Fails: Would not raise ConfigError.
        """
        hosted_env = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app:pass@external-db.example.com:5432/railway",
            "REDIS_URL": "redis://default:pass@redis.railway.internal:6379/0",
            "AUTH_ISSUER": "https://auth.example.com/",
            "AUTH_AUDIENCE": "api://cleaning-app",
            "HOSPITABLE_READ_ONLY": "true",
            "WORKSPACE_SIGNUP_ENABLED": "true",
            "FIELD_ENCRYPTION_KEY_FILE": "/data/private/field-key",
            "CLEANER_PORTAL_BASE_URL": "https://app.example.com",
            "API_ALLOWED_HOSTS": "api.example.com",
            "WEB_PROXY_SECRET": "a" * 43,
            "AUTH_PROVIDER": "workspace",
            "RESERVATION_PROVIDER": "hospitable",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "MIGRATION_DATABASE_URL": "",
            "HOSPITABLE_CREDENTIAL_SECRET_REF": "",
            "HOSPITABLE_WEBHOOK_SECRET_REF": "",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
            "TWILIO_MESSAGING_SERVICE_SID": "",
            "GOOGLE_MAPS_API_KEY": "",
        }

        clear_settings_cache()
        with mock.patch.dict(os.environ, hosted_env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.hosted_workspace is True
            with pytest.raises(ConfigError) as exc_info:
                validate_startup(settings)
            assert exc_info.value.code == "hosted_workspace_isolation_required"

    def test_hosted_refuses_non_railway_internal_redis_host(self) -> None:
        """Hosted mode refuses when REDIS_URL host doesn't end in .railway.internal.

        Mutation: Remove cache.hostname.endswith(".railway.internal") check at config.py.
        Fails: Would not raise ConfigError.
        """
        hosted_env = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app:pass@db.railway.internal:5432/railway",
            "REDIS_URL": "redis://default:pass@external-redis.example.com:6379/0",
            "AUTH_ISSUER": "https://auth.example.com/",
            "AUTH_AUDIENCE": "api://cleaning-app",
            "HOSPITABLE_READ_ONLY": "true",
            "WORKSPACE_SIGNUP_ENABLED": "true",
            "FIELD_ENCRYPTION_KEY_FILE": "/data/private/field-key",
            "CLEANER_PORTAL_BASE_URL": "https://app.example.com",
            "API_ALLOWED_HOSTS": "api.example.com",
            "WEB_PROXY_SECRET": "a" * 43,
            "AUTH_PROVIDER": "workspace",
            "RESERVATION_PROVIDER": "hospitable",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "MIGRATION_DATABASE_URL": "",
            "HOSPITABLE_CREDENTIAL_SECRET_REF": "",
            "HOSPITABLE_WEBHOOK_SECRET_REF": "",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
            "TWILIO_MESSAGING_SERVICE_SID": "",
            "GOOGLE_MAPS_API_KEY": "",
        }

        clear_settings_cache()
        with mock.patch.dict(os.environ, hosted_env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.hosted_workspace is True
            with pytest.raises(ConfigError) as exc_info:
                validate_startup(settings)
            assert exc_info.value.code == "hosted_workspace_isolation_required"


class TestHostedStartupMigrateThenPop:
    """Test that hosted_startup.py pops MIGRATION_DATABASE_URL after alembic, before execve.

    This tests the specific behavior of hosted_startup.py lines 74-85:
    1. subprocess.run calls alembic with MIGRATION_DATABASE_URL in environ
    2. os.environ.pop removes MIGRATION_DATABASE_URL
    3. os.execve receives an env dict without MIGRATION_DATABASE_URL
    """

    def test_migration_url_present_during_alembic_absent_at_execve(self) -> None:
        """hosted_startup runs alembic with MIGRATION_DATABASE_URL, then pops before execve.

        Mutation: Remove os.environ.pop("MIGRATION_DATABASE_URL", None) at hosted_startup.py:80.
        Fails: execve would receive MIGRATION_DATABASE_URL in its environment.
        """
        from pathlib import Path

        import hosted_startup

        alembic_saw_migration_url: list[bool] = []
        execve_env: dict[str, str] = {}

        def mock_subprocess_run(args: list[str], **kwargs: object) -> mock.MagicMock:
            if "alembic" in str(args):
                alembic_saw_migration_url.append("MIGRATION_DATABASE_URL" in os.environ)
            result = mock.MagicMock()
            result.returncode = 0
            return result

        def mock_execve(path: str, args: list[str], env: dict[str, str]) -> None:
            execve_env.update(env)
            raise SystemExit(0)

        def mock_get_settings() -> mock.MagicMock:
            settings = mock.MagicMock()
            settings.hosted_workspace = True
            return settings

        def mock_validate_startup(settings: object) -> None:
            pass

        test_env = {
            "ROUTING_SERVICE_URL": "http://osrm.railway.internal:5000",
            "FIELD_ENCRYPTION_KEY": "test-key",
            "GMAIL_CREDENTIALS_JSON": "{}",
            "ROUTING_MANIFEST_JSON": "{}",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app:pass@db.railway.internal:5432/railway",
            "REDIS_URL": "redis://default:pass@redis.railway.internal:6379/0",
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://postgres:pass@db.railway.internal:5432/railway",
            "PORT": "8000",
        }

        with (
            mock.patch.dict(os.environ, test_env, clear=True),
            mock.patch.object(sys, "argv", ["hosted_startup.py"]),
            mock.patch("os.getuid", return_value=10001),
            mock.patch("os.getgid", return_value=10001),
            mock.patch("subprocess.run", side_effect=mock_subprocess_run),
            mock.patch("os.execve", side_effect=mock_execve),
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "chmod"),
            mock.patch.object(Path, "is_symlink", return_value=False),
            mock.patch("os.open", return_value=99),
            mock.patch("os.fdopen", mock.mock_open()),
            mock.patch("os.fsync"),
            mock.patch("hosted_startup.get_settings", side_effect=mock_get_settings),
            mock.patch("hosted_startup.validate_startup", side_effect=mock_validate_startup),
        ):
            with pytest.raises(SystemExit):
                hosted_startup.main()

        assert len(alembic_saw_migration_url) == 1, "subprocess.run should be called once for alembic"
        assert alembic_saw_migration_url[0] is True, "MIGRATION_DATABASE_URL should be present during alembic"
        assert "MIGRATION_DATABASE_URL" not in execve_env, (
            "MIGRATION_DATABASE_URL should be absent from execve environment"
        )
