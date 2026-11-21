"""Windows local demo launcher. Own services, ports, database and synthetic data only.

Run with backend/.venv/Scripts/python.exe infra/local/demo.py [--reset].
PostgreSQL 17, Node 22, uv, and WSL Ubuntu with redis-server must be installed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local-demo"
PG = Path(os.environ.get("DEMO_POSTGRES_BIN", r"C:\Program Files\PostgreSQL\17\bin"))
PYTHON = ROOT / "backend/.venv/Scripts/python.exe"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    kwargs.setdefault("text", True)
    if kwargs.get("capture_output"):
        return subprocess.run(args, check=True, creationflags=CREATE_NO_WINDOW, **kwargs)
    # Daemons can inherit pipe handles. A file avoids waiting for pipe EOF after
    # pg_ctl/WSL exit while their background service remains alive.
    with (STATE / "setup.log").open("a", encoding="utf-8") as log:
        try:
            return subprocess.run(
                args,
                check=True,
                creationflags=CREATE_NO_WINDOW,
                stdout=log,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
        except subprocess.CalledProcessError as failure:
            raise RuntimeError("Demo setup failed. See .local-demo/setup.log for details.") from failure


def available(port: int) -> bool:
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) != 0


def environment() -> dict[str, str]:
    env = os.environ.copy()
    secret_file = STATE / "secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48))
    env.update(
        {
            "LOCAL_DEMO": "1",
            "LOCAL_DEMO_ROUTING_BASELINE": "1" if (STATE / "driving-baseline.json").exists() else "0",
            "APP_ENV": "development",
            "AUTH_PROVIDER": "mock",
            "RESERVATION_PROVIDER": "mock",
            "SMS_PROVIDER": "mock",
            "ROUTING_PROVIDER": "mock",
            "PRODUCTION_DISPATCH_ENABLED": "false",
            "PRODUCTION_INTEGRATIONS_ENABLED": "false",
            "DATABASE_URL": "postgresql+psycopg://cleaning_app@127.0.0.1:55439/cleaning_demo",
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://cleaning_migrator@127.0.0.1:55439/cleaning_demo",
            "REDIS_URL": "redis://127.0.0.1:6398/7",
            "AUTH_ISSUER": "http://localhost/mock-issuer",
            "AUTH_AUDIENCE": "cleaning-local",
            "AUTH_MOCK_SECRET": secret_file.read_text(),
            "CLEANING_API_ORIGIN": "http://127.0.0.1:8108",
            "CORS_ORIGINS": "http://127.0.0.1:3108",
        }
    )
    # Avoid competing with other local applications for an unbounded V8 heap.
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=768")
    for key in (
        "HOSPITABLE_CREDENTIAL_SECRET_REF",
        "HOSPITABLE_WEBHOOK_SECRET_REF",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_MESSAGING_SERVICE_SID",
        "GOOGLE_MAPS_API_KEY",
    ):
        env[key] = ""
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Recreate only the dedicated cleaning_demo database",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Ask the running launcher to stop its own demo services",
    )
    args = parser.parse_args()
    STATE.mkdir(exist_ok=True)
    stop_file = STATE / "stop-request"
    if args.stop:
        stop_file.touch()
        print("Requested demo shutdown. The running launcher will stop its own services.")
        return
    stop_file.unlink(missing_ok=True)
    env = environment()
    if not PYTHON.exists() or not (PG / "pg_ctl.exe").exists():
        raise SystemExit("Install backend dependencies with uv sync and PostgreSQL 17 first. See docs/LOCAL_DEMO.md.")
    node = shutil.which("node")
    if not node:
        candidates = list((Path(os.environ["TEMP"]) / "airbnb-m7-node").glob("node-v22*-win-x64/node.exe"))
        node = str(candidates[0]) if candidates else None
    if not node:
        raise SystemExit("Node 22 is required on PATH.")
    if not (ROOT / "frontend/node_modules/next/dist/bin/next").exists():
        raise SystemExit("Run npm ci in frontend first.")
    for port in (3108, 8108, 55439, 6398):
        if not available(port):
            raise SystemExit(f"Demo port {port} is occupied. Stop the previous demo; no existing service was changed.")
    data = STATE / "postgres"
    if not data.exists():
        run(
            [
                str(PG / "initdb.exe"),
                "-D",
                str(data),
                "-U",
                "demo_admin",
                "--auth=trust",
                "--encoding=UTF8",
            ]
        )
    children: list[subprocess.Popen] = []
    postgres_started = redis_started = False
    logs = []
    try:
        run(
            [
                str(PG / "pg_ctl.exe"),
                "-D",
                str(data),
                "-l",
                str(STATE / "postgres.log"),
                "-o",
                "-h 127.0.0.1 -p 55439",
                "-w",
                "start",
            ]
        )
        postgres_started = True
        run(
            [
                "wsl.exe",
                "-d",
                "Ubuntu",
                "--",
                "redis-server",
                "--port",
                "6398",
                "--bind",
                "127.0.0.1",
                "--save",
                "",
                "--appendonly",
                "no",
                "--daemonize",
                "yes",
                "--logfile",
                "/tmp/turnover-demo-redis.log",
            ]
        )
        redis_started = True
        sql = """DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='cleaning_migrator') THEN
            CREATE ROLE cleaning_migrator LOGIN;
          END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='cleaning_app') THEN CREATE ROLE cleaning_app LOGIN; END IF;
        END $$;"""
        base = ["-h", "127.0.0.1", "-p", "55439", "-U", "demo_admin"]
        run(
            [
                str(PG / "psql.exe"),
                *base,
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ]
        )
        exists = run(
            [
                str(PG / "psql.exe"),
                *base,
                "-d",
                "postgres",
                "-tAc",
                "SELECT 1 FROM pg_database WHERE datname='cleaning_demo'",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if exists and args.reset:
            # Literal target, private cluster, verified loopback port; no computed database deletion.
            run([str(PG / "dropdb.exe"), *base, "cleaning_demo"])
            exists = ""
        if not exists:
            run(
                [
                    str(PG / "createdb.exe"),
                    *base,
                    "-O",
                    "cleaning_migrator",
                    "cleaning_demo",
                ]
            )
        run(
            [str(PYTHON), "-m", "alembic", "upgrade", "head"],
            cwd=ROOT / "backend",
            env=env,
        )
        if not exists:
            run([str(PYTHON), "-m", "app.demo.seed"], cwd=ROOT / "backend", env=env)
        commands = [
            (
                [
                    str(PYTHON),
                    "-m",
                    "uvicorn",
                    "app.demo.server:factory",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8108",
                ],
                ROOT / "backend",
                "api.log",
            ),
            (
                [
                    node,
                    "node_modules/next/dist/bin/next",
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3108",
                ],
                ROOT / "frontend",
                "web.log",
            ),
        ]
        for command, cwd, filename in commands:
            log = (STATE / filename).open("w")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=CREATE_NO_WINDOW,
                )
            )
        for url in ("http://127.0.0.1:8108/v1/health/ready", "http://127.0.0.1:3108"):
            deadline = time.monotonic() + 120
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("Demo process exited. See .local-demo/api.log and web.log.")
                try:
                    with urlopen(url, timeout=3) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Timed out starting {url}; see .local-demo logs.")
                time.sleep(1)
        print(
            "\nDemo ready: http://127.0.0.1:3108 · Enter demo as manager.\n"
            "Keep this terminal open. Ctrl+C stops demo services.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            if stop_file.exists():
                stop_file.unlink()
                return
            time.sleep(1)
        raise RuntimeError("A demo server exited unexpectedly. See .local-demo/api.log and web.log.")
    except KeyboardInterrupt:
        print("Stopping local demo…", flush=True)
    finally:
        for child in children:
            if child.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    creationflags=CREATE_NO_WINDOW,
                    capture_output=True,
                    check=False,
                )
        for log in logs:
            log.close()
        if redis_started:
            subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu",
                    "--",
                    "redis-cli",
                    "-p",
                    "6398",
                    "shutdown",
                    "nosave",
                ],
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
        if postgres_started:
            subprocess.run(
                [str(PG / "pg_ctl.exe"), "-D", str(data), "-m", "fast", "-w", "stop"],
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )


if __name__ == "__main__":
    main()
