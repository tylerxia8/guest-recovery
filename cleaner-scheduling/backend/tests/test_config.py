from __future__ import annotations

import pytest
from app.config import Settings, validate_startup
from app.domain.errors import ConfigError
from pydantic import ValidationError


def base_settings(**overrides: object) -> Settings:
    data: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+psycopg://cleaning_app:local-dev-only@127.0.0.1:5432/cleaning",
        "migration_database_url": ("postgresql+psycopg://cleaning_migrator:local-dev-only@127.0.0.1:5432/cleaning"),
        "redis_url": "redis://127.0.0.1:6379/1",
        "auth_issuer": "http://localhost/mock-issuer",
        "auth_audience": "cleaning-local",
        "auth_mock_secret": "test-only-mock-secret-not-a-credential",
        "reservation_provider": "mock",
        "sms_provider": "mock",
        "routing_provider": "mock",
        "auth_provider": "mock",
        "production_integrations_enabled": False,
        "production_dispatch_enabled": False,
    }
    data.update(overrides)
    return Settings(**data)


def test_empty_database_url_fails_closed() -> None:
    with pytest.raises(ValidationError):
        base_settings(database_url="  ")


def test_operational_timezone_is_fixed() -> None:
    with pytest.raises(ValidationError):
        base_settings(operational_timezone="UTC")


def test_production_rejects_mock_auth() -> None:
    settings = base_settings(app_env="production", auth_provider="mock")
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "mock_auth_forbidden"


def test_production_rejects_mock_providers_and_unimplemented_adapters() -> None:
    settings = base_settings(
        app_env="production",
        auth_provider="oidc",
        auth_mock_secret="",
        reservation_provider="hospitable",
        sms_provider="twilio",
        routing_provider="google",
    )
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "production_adapter_unavailable"


def test_staging_rejects_mock_provider() -> None:
    settings = base_settings(app_env="staging", auth_provider="oidc", auth_mock_secret="", sms_provider="mock")
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "mock_provider_forbidden"


def test_development_requires_mock_providers() -> None:
    settings = base_settings(app_env="development", reservation_provider="hospitable")
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "development_requires_mocks"


def test_provider_credentials_must_be_absent() -> None:
    secret = "not-a-real-token"
    settings = base_settings(twilio_auth_token=secret)
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "credentials_not_permitted"
    assert secret not in caught.value.message


def test_dispatch_flag_requires_integrations_flag() -> None:
    settings = base_settings(
        production_dispatch_enabled=True,
        production_integrations_enabled=False,
    )
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "dispatch_requires_integrations"


def test_short_mock_secret_is_rejected() -> None:
    settings = base_settings(auth_mock_secret="too-short")
    with pytest.raises(ConfigError) as caught:
        validate_startup(settings)
    assert caught.value.code == "mock_secret_required"
