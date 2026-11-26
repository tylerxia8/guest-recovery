"""Wait for Postgres, migrate, and serve the Maintenance API."""

import os
import subprocess
import sys
import time

from sqlalchemy import create_engine, text

from grok_bot.seed import ensure_demo_maintenance, ensure_demo_statement, seed_demo
from grok_bot.settings import get_settings


def _wait() -> None:
    url = os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    import psycopg

    for _ in range(60):
        try:
            psycopg.connect(url, connect_timeout=3).close()
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("Maintenance database did not become ready")


def main() -> None:
    settings = get_settings()
    demo = settings.demo_fixture == "sample"
    if demo and not settings.allows_demo_session:
        raise SystemExit("Demo startup requires a fictional database and a local environment.")
    _wait()
    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd="/app", timeout=120)
    if demo:
        url = settings.migration_database_url
        engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000 -c lock_timeout=5000"})
        try:
            with engine.connect() as conn:
                existing = conn.execute(text("SELECT count(*) FROM organizations")).scalar()
            if int(existing or 0) == 0:
                seed_demo(url, settings.app_env, settings.session_secret)
            ensure_demo_statement(engine, settings.app_env)
            ensure_demo_maintenance(engine, settings.app_env)
        finally:
            engine.dispose()
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "grok_bot.main:app", "--host", os.environ.get("API_HOST", "0.0.0.0"), "--port", "8000"],
    )


if __name__ == "__main__":
    main()
