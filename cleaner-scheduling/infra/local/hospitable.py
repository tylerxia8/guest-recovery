from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "private-data"
STATE = PRIVATE / "hospitable"
ENV = PRIVATE / "hospitable.env"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ENV),
    "-f",
    str(ROOT / "infra/local/hospitable-compose.yml"),
]


def main() -> None:
    os.umask(0o077)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    STATE.mkdir(mode=0o700, exist_ok=True)
    (STATE / "session").mkdir(mode=0o700, exist_ok=True)
    for directory in (PRIVATE, STATE, STATE / "session"):
        directory.chmod(0o700)
    if not ENV.exists():
        password = secrets.token_hex(32)
        ENV.write_text(
            f"LOCAL_DATABASE_PASSWORD={password}\nLOCAL_AUTH_SECRET={secrets.token_hex(48)}\n"
            f"LOCAL_UID={os.getuid()}\nLOCAL_GID={os.getgid()}\n"
        )
        (PRIVATE / "hospitable-init.sql").write_text(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cleaning_app') THEN "
            f"CREATE ROLE cleaning_app LOGIN PASSWORD '{password}'; END IF; END $$;\n"
            "GRANT CONNECT ON DATABASE cleaning_hospitable TO cleaning_app;\n"
        )
    ENV.chmod(0o600)
    (PRIVATE / "hospitable-init.sql").chmod(0o600)
    field_key = STATE / "field-key"
    fingerprint = STATE / "field-key.sha256"
    if field_key.is_symlink():
        raise SystemExit("Field encryption key must not be a symbolic link.")
    if not field_key.exists():
        if fingerprint.exists():
            raise SystemExit("Restore the original field-key from your private backup before starting.")
        descriptor = os.open(field_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_urlsafe(48))
    field_key.chmod(0o600)
    digest = hashlib.sha256(field_key.read_bytes()).hexdigest()
    if fingerprint.exists() and fingerprint.read_text() != digest:
        raise SystemExit("Field encryption key changed. Restore the original key; no data was re-encrypted.")
    if not fingerprint.exists():
        fingerprint.write_text(digest)
    token = Path.home() / ".hospitable-read-token"
    if not token.is_file() or token.stat().st_mode & 0o077:
        raise SystemExit("Save an owner-only ~/.hospitable-read-token file first.")
    if not (STATE / "routing" / "manifest.json").is_file():
        subprocess.run([sys.executable, str(ROOT / "infra/local/prepare_free_routing.py")], check=True, timeout=2400)
    log = STATE / "startup.log"
    with log.open("a") as output:
        for args in (
            ["build"],
            ["up", "-d", "--wait", "hospitable-db", "hospitable-cache", "hospitable-routing"],
        ):
            subprocess.run(
                [*COMPOSE, *args],
                check=True,
                timeout=900,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        with (PRIVATE / "hospitable-init.sql").open() as source:
            subprocess.run(
                [
                    *COMPOSE,
                    "exec",
                    "-T",
                    "hospitable-db",
                    "psql",
                    "-U",
                    "cleaning_migrator",
                    "-d",
                    "cleaning_hospitable",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                stdin=source,
                check=True,
                timeout=30,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        backup = STATE / f"before-migration-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.dump"
        with backup.open("wb") as destination:
            subprocess.run(
                [
                    *COMPOSE,
                    "exec",
                    "-T",
                    "hospitable-db",
                    "pg_dump",
                    "-U",
                    "cleaning_migrator",
                    "-d",
                    "cleaning_hospitable",
                    "-Fc",
                ],
                check=True,
                timeout=120,
                stdout=destination,
                stderr=output,
            )
        backup.with_suffix(".field-key").write_bytes(field_key.read_bytes())
        for args in (
            ["stop", "hospitable-web", "hospitable-api"],
            ["run", "--rm", "hospitable-api", "alembic", "upgrade", "head"],
            ["run", "--rm", "hospitable-api", "python", "-m", "app.local_hospitable"],
            ["up", "-d", "hospitable-api", "hospitable-web"],
        ):
            subprocess.run(
                [*COMPOSE, *args],
                check=True,
                timeout=180,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
    print("Read-only Hospitable workspace: http://localhost:3011/reservations")
    print("Click Connect to Hospitable. Sync runs every 15 minutes while the API is running.")
    print("Local startup log: " + str(log))


if __name__ == "__main__":
    main()
