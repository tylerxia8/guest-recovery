"""Explicit synthetic browser fixture, only for isolated test databases.

Run after migrations/seed in the frontend CI job. Never an application startup job.
"""

from app.api.app_factory import create_app
from app.config import AppEnv, get_settings
from fastapi.testclient import TestClient
from tests.completed_fixture import complete_fixture


def main() -> None:
    settings = get_settings()
    if (
        settings.app_env != AppEnv.test
        or settings.production_integrations_enabled
        or settings.production_dispatch_enabled
    ):
        raise RuntimeError("Browser fixtures require isolated APP_ENV=test with production flags off")
    with TestClient(create_app(settings)) as client:
        complete_fixture(client, two=True)
    print("Seeded synthetic completed work for browser acceptance tests; no carrier SMS or transfers.")


if __name__ == "__main__":
    main()
