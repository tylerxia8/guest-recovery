from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.domain.errors import ConfigError

_SECRET_ENV_FIELDS = (
    "hospitable_credential_secret_ref",
    "hospitable_webhook_secret_ref",
    "twilio_account_sid",
    "twilio_auth_token",
    "twilio_messaging_service_sid",
    "google_maps_api_key",
)


class AppEnv(StrEnum):
    development = "development"
    test = "test"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    _field_key: str | None = PrivateAttr(default=None)

    app_env: AppEnv = AppEnv.development
    operational_timezone: str = "America/New_York"
    reservation_provider: str = "mock"
    sms_provider: str = "mock"
    routing_provider: str = "mock"
    auth_provider: str = "mock"
    workspace_signup_enabled: bool = False
    workspace_registration_enabled: bool = True
    workspace_invite_signup_enabled: bool = False
    workspace_invite_email: str = Field(default="", repr=False)
    workspace_invite_token_hash: str = Field(default="", repr=False)
    workspace_invite_expires_at: datetime | None = None
    api_allowed_hosts: str = ""
    web_proxy_secret: str = Field(default="", repr=False)
    workspace_internal_pat_enabled: bool = False
    workspace_pilot_pat_enabled: bool = False
    production_integrations_enabled: bool = False
    production_dispatch_enabled: bool = False
    database_url: str = ""
    migration_database_url: str = ""
    redis_url: str = ""
    auth_issuer: str = ""
    auth_audience: str = ""
    auth_mock_secret: str = ""
    hospitable_credential_secret_ref: str = ""
    hospitable_webhook_secret_ref: str = ""
    hospitable_read_only: bool = False
    local_free_routing: bool = False
    local_scheduling_enabled: bool = False
    local_email_automation_enabled: bool = False
    hospitable_guest_departure_enabled: bool = False
    cleaner_portal_base_url: str = "http://localhost:3011"
    hospitable_token_file: str = ""
    field_encryption_key_file: str = ""
    gmail_credentials_file: str = ""
    gmail_test_send_enabled: bool = False
    gmail_assignment_send_enabled: bool = False
    gmail_onboarding_send_enabled: bool = False
    security_alert_email_enabled: bool = False
    security_alert_email: str = Field(default="", repr=False)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_messaging_service_sid: str = ""
    google_maps_api_key: str = ""
    availability_request_local_time: str = "17:00"
    availability_cutoff_local_time: str = "17:30"
    dispatch_local_time: str = "20:00"
    ack_reminder_minutes: int = 30
    ack_escalation_minutes: int = 60
    readiness_buffer_minutes: int = 30
    auto_reassign_missing_ack: bool = False
    policy_approval_required: bool = True
    solver_time_limit_seconds: int = 30
    reservation_reconcile_minutes: int = 15
    routing_cache_ttl_minutes: int = 60
    integration_stale_threshold_minutes: int = 30
    cors_origins: str = "http://127.0.0.1:3000"
    write_rate_limit_per_minute: int = 30
    read_rate_limit_per_minute: int = 120
    preview_rate_limit_per_minute: int = 5
    idempotency_retention_hours: int = 72

    @field_validator(
        "database_url",
        "redis_url",
        "auth_issuer",
        "auth_audience",
    )
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("migration_database_url")
    @classmethod
    def _strip_migration_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("operational_timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if value != "America/New_York":
            raise ValueError("operational timezone must be America/New_York")
        return value

    @field_validator("cleaner_portal_base_url")
    @classmethod
    def _cleaner_portal_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.hostname
            or parsed.port == 0
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (
                parsed.scheme != "https"
                and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
            )
        ):
            raise ValueError("Cleaner portal requires HTTPS, except for local development.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _bounds(self) -> Settings:
        if self.workspace_invite_signup_enabled or self.workspace_pilot_pat_enabled:
            from app.services.email_addresses import normalize_email

            email = normalize_email(self.workspace_invite_email)
            if (
                not self.workspace_signup_enabled
                or self.workspace_registration_enabled
                or email is None
                or email.lower() != self.workspace_invite_email
                or re.fullmatch(r"[0-9a-f]{64}", self.workspace_invite_token_hash) is None
                or self.workspace_invite_expires_at is None
                or self.workspace_invite_expires_at.utcoffset() is None
            ):
                raise ValueError(
                    "Invite signup requires a normalized email, token hash, aware expiry and closed registration"
                )
        if self.ack_escalation_minutes <= self.ack_reminder_minutes:
            raise ValueError("ack escalation must be greater than the reminder interval")
        if self.idempotency_retention_hours < 72:
            raise ValueError("idempotency retention must be at least 72 hours")
        for name in (
            "write_rate_limit_per_minute",
            "read_rate_limit_per_minute",
            "preview_rate_limit_per_minute",
            "ack_reminder_minutes",
            "readiness_buffer_minutes",
            "solver_time_limit_seconds",
            "reservation_reconcile_minutes",
            "routing_cache_ttl_minutes",
            "integration_stale_threshold_minutes",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        return self

    @property
    def protected_environment(self) -> bool:
        return self.app_env in {AppEnv.production, AppEnv.staging}

    @property
    def hosted_workspace(self) -> bool:
        return self.protected_environment and self.workspace_signup_enabled and self.hospitable_read_only

    @property
    def api_allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.api_allowed_hosts.split(",") if host.strip()]

    @property
    def field_encryption_secret(self) -> str:
        if self.hospitable_read_only:
            from app.services.field_cipher import read_private_key

            if self._field_key is None:
                self._field_key = read_private_key(self.field_encryption_key_file)
            return self._field_key
        return self.auth_mock_secret

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def provider_names(self) -> dict[str, str]:
        return {
            "reservation_provider": self.reservation_provider,
            "sms_provider": self.sms_provider,
            "routing_provider": self.routing_provider,
            "auth_provider": self.auth_provider,
        }


def validate_startup(settings: Settings) -> None:
    if settings.security_alert_email_enabled:
        from app.services.email_addresses import normalize_email
        from app.services.gmail import read_credentials

        try:
            recipient = normalize_email(settings.security_alert_email)
            if not settings.hosted_workspace or recipient is None or not settings.gmail_credentials_file:
                raise ValueError("Security alerts are not configured")
            read_credentials(settings.gmail_credentials_file)
            settings.security_alert_email = recipient
        except (OSError, ValueError):
            raise ConfigError(
                "security_alert_email_unavailable",
                "Security alerts require a hosted workspace, valid recipient and private Gmail credentials.",
            ) from None
    if settings.gmail_onboarding_send_enabled and not settings.workspace_signup_enabled:
        raise ConfigError("onboarding_email_isolation_required", "Onboarding email requires workspace signup mode.")
    if settings.workspace_internal_pat_enabled and not settings.workspace_signup_enabled:
        raise ConfigError(
            "internal_connection_requires_signup", "Internal connection setup requires workspace signup mode."
        )
    if settings.workspace_signup_enabled and not settings.hospitable_read_only:
        raise ConfigError("workspace_signup_isolation_required", "Workspace signup requires read-only Hospitable mode.")
    if settings.local_email_automation_enabled and not (
        settings.local_scheduling_enabled and settings.gmail_assignment_send_enabled and settings.gmail_credentials_file
    ):
        raise ConfigError(
            "email_automation_not_configured", "Email automation requires local scheduling and Gmail setup."
        )
    if settings.local_scheduling_enabled and not (settings.hospitable_read_only and settings.local_free_routing):
        raise ConfigError(
            "local_scheduling_isolation_required", "Local scheduling requires read-only imports and local routing."
        )
    if (
        settings.gmail_credentials_file
        or settings.gmail_test_send_enabled
        or settings.gmail_assignment_send_enabled
        or settings.gmail_onboarding_send_enabled
    ) and not settings.hospitable_read_only:
        raise ConfigError("gmail_isolation_required", "Gmail setup requires the isolated local workspace.")
    if (
        settings.gmail_test_send_enabled
        or settings.gmail_assignment_send_enabled
        or settings.gmail_onboarding_send_enabled
    ) and not settings.gmail_credentials_file:
        raise ConfigError("gmail_credentials_required", "Gmail test sending requires a private credentials file.")
    if settings.local_free_routing and not settings.hospitable_read_only:
        raise ConfigError(
            "local_routing_isolation_required", "Free local routing requires the isolated Hospitable workspace."
        )
    if settings.hospitable_read_only:
        database = make_url(settings.database_url)
        migration = make_url(settings.migration_database_url) if settings.migration_database_url else None
        cache = urlsplit(settings.redis_url)
        import os

        if settings.hosted_workspace:
            migration_invalid = migration is not None and (
                migration.get_backend_name() != "postgresql"
                or not migration.username
                or database.username == migration.username
                or (migration.host, migration.port, migration.database)
                != (database.host, database.port, database.database)
            )
            if (
                len(settings.web_proxy_secret) < 43
                or settings.auth_provider != "workspace"
                or settings.reservation_provider != "hospitable"
                or settings.sms_provider != "mock"
                or settings.routing_provider != "mock"
                or settings.production_dispatch_enabled
                or settings.production_integrations_enabled
                or settings.auth_mock_secret
                or settings.workspace_internal_pat_enabled
                or not settings.field_encryption_key_file
                or urlsplit(settings.cleaner_portal_base_url).scheme != "https"
                or not settings.api_allowed_host_list
                or any(
                    re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host) is None
                    for host in settings.api_allowed_host_list
                )
                or database.get_backend_name() != "postgresql"
                or migration_invalid
                or not database.database
                or not database.host
                or not database.host.endswith(".railway.internal")
                or database.username != "cleaning_app"
                or cache.scheme not in {"redis", "rediss"}
                or not cache.hostname
                or not cache.hostname.endswith(".railway.internal")
                or os.getenv("LOCAL_DEMO")
                or any(str(getattr(settings, name)).strip() for name in _SECRET_ENV_FIELDS)
            ):
                raise ConfigError(
                    "hosted_workspace_isolation_required",
                    "Hosted workspace requires private, isolated internal access.",
                )
        elif (
            settings.app_env != AppEnv.development
            or settings.reservation_provider != "hospitable"
            or settings.sms_provider != "mock"
            or settings.routing_provider != "mock"
            or settings.auth_provider != "mock"
            or settings.production_dispatch_enabled
            or settings.production_integrations_enabled
            or not settings.hospitable_token_file
            or not settings.field_encryption_key_file
            or len(settings.auth_mock_secret) < 32
            or database.database != "cleaning_hospitable"
            or migration is None
            or (migration.host, migration.port, migration.database) != (database.host, database.port, database.database)
            or database.host not in {"127.0.0.1", "localhost", "hospitable-db"}
            or cache.hostname not in {"127.0.0.1", "localhost", "hospitable-cache"}
            or cache.path != "/6"
            or os.getenv("LOCAL_DEMO") == "1"
            or any(str(getattr(settings, name)).strip() for name in _SECRET_ENV_FIELDS)
        ):
            raise ConfigError("read_only_isolation_required", "Hospitable reads require an isolated local workspace.")
        try:
            encryption_secret = settings.field_encryption_secret
        except (OSError, ValueError):
            raise ConfigError(
                "private_encryption_key_required", "A private field encryption key is required."
            ) from None
        if encryption_secret == settings.auth_mock_secret:
            raise ConfigError("separate_encryption_key_required", "Field encryption must use a separate key.")
        return
    for field_name in _SECRET_ENV_FIELDS:
        if str(getattr(settings, field_name)).strip():
            raise ConfigError(
                "credentials_not_permitted",
                f"{field_name} must be empty until a verified production adapter exists",
            )
    if settings.protected_environment:
        if settings.auth_provider == "mock":
            raise ConfigError(
                "mock_auth_forbidden",
                "production and staging reject mock authentication",
            )
        if settings.auth_mock_secret.strip():
            raise ConfigError(
                "mock_secret_forbidden",
                "AUTH_MOCK_SECRET must be empty outside local and test environments",
            )
        for name, value in settings.provider_names.items():
            if value == "mock":
                raise ConfigError(
                    "mock_provider_forbidden",
                    f"{name} must not be mock in production or staging",
                )
        raise ConfigError(
            "production_adapter_unavailable",
            "M1 has no verified production adapter or OIDC verifier; refusing to start",
        )
    if settings.auth_provider != "mock":
        raise ConfigError(
            "development_requires_mocks",
            "development and test must use mock auth in M1",
        )
    for name, value in settings.provider_names.items():
        if name == "auth_provider":
            continue
        if value != "mock":
            raise ConfigError(
                "development_requires_mocks",
                f"{name} must be mock in development and test",
            )
    if len(settings.auth_mock_secret) < 32:
        raise ConfigError(
            "mock_secret_required",
            "AUTH_MOCK_SECRET must be at least 32 characters in local and test environments",
        )
    if settings.production_dispatch_enabled and not settings.production_integrations_enabled:
        raise ConfigError(
            "dispatch_requires_integrations",
            "production dispatch cannot be enabled unless production integrations are enabled",
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    validate_startup(settings)
    return settings


def clear_settings_cache() -> None:
    get_settings.cache_clear()
