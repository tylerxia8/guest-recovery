import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher():
    path = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.py"
    spec = importlib.util.spec_from_file_location("maintenance_container_entrypoint", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_migrates_and_serves_without_demo_data(launcher, monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "fixture-only")
    monkeypatch.setattr(
        launcher, "get_settings",
        lambda: SimpleNamespace(demo_fixture="unset", migration_database_url="fixture-only"),
    )
    wait = Mock()
    migrate = Mock()
    start = Mock()
    seed_engine = Mock()
    monkeypatch.setattr(launcher, "_wait", wait)
    monkeypatch.setattr(launcher.subprocess, "check_call", migrate)
    monkeypatch.setattr(launcher.os, "execvp", start)
    monkeypatch.setattr(launcher, "create_engine", seed_engine)

    launcher.main()

    wait.assert_called_once_with()
    assert migrate.call_args.args[0][1:] == ["-m", "alembic", "upgrade", "head"]
    assert "grok_bot.main:app" in start.call_args.args[1]
    assert "MIGRATION_DATABASE_URL" not in launcher.os.environ
    seed_engine.assert_not_called()


def test_demo_cannot_migrate_an_operational_database(launcher, monkeypatch):
    monkeypatch.setattr(
        launcher, "get_settings", lambda: SimpleNamespace(demo_fixture="sample", allows_demo_session=False)
    )
    wait = Mock()
    monkeypatch.setattr(launcher, "_wait", wait)
    with pytest.raises(SystemExit, match="fictional database"):
        launcher.main()
    wait.assert_not_called()
