from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from app.config import get_settings, validate_startup
from app.logging_redaction import HostedFormatter


def main() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(HostedFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if sys.argv[1:] == ["serve"]:
        serve()
        return
    if sys.argv[1:]:
        raise ValueError("Unexpected startup arguments.")
    routing = urlsplit(os.environ["ROUTING_SERVICE_URL"])
    if routing.scheme != "http" or not routing.hostname or not routing.hostname.endswith(".railway.internal"):
        raise RuntimeError("Road routing requires the private hosted service.")
    directory = Path("/data/private")
    if directory.is_symlink():
        raise RuntimeError("Private storage cannot be a symbolic link.")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    if os.getuid() == 0:
        os.chown(directory, 0, 0)
    for variable, filename in (
        ("FIELD_ENCRYPTION_KEY", "field-key"),
        ("GMAIL_CREDENTIALS_JSON", "gmail.json"),
        ("ROUTING_MANIFEST_JSON", "manifest.json"),
    ):
        value = os.environ.pop(variable).encode()
        path = directory / filename
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                if stream.read(65_537) != value:
                    raise RuntimeError("Private configuration changed; a reviewed transition is required.") from None
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        path.chmod(0o600)
    os.environ["FIELD_ENCRYPTION_KEY_FILE"] = str(directory / "field-key")
    os.environ["GMAIL_CREDENTIALS_FILE"] = str(directory / "gmail.json")
    os.environ["ROUTING_MANIFEST_FILE"] = str(directory / "manifest.json")
    if os.getuid() == 0:
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Private storage contains an unexpected entry.")
            os.chown(path, 10001, 10001)
        os.chown(directory, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    if os.getuid() != 10001 or os.getgid() != 10001:
        raise RuntimeError("Hosted API requires the unprivileged application identity.")
    settings = get_settings()
    if not settings.hosted_workspace:
        raise RuntimeError("Hosted startup requires isolated workspace accounts.")
    validate_startup(settings)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parent,
        timeout=180,
        check=True,
    )
    os.environ.pop("MIGRATION_DATABASE_URL", None)
    os.execve(
        sys.executable,
        [sys.executable, str(Path(__file__).resolve()), "serve"],
        dict(os.environ),
    )


def serve() -> None:
    settings = get_settings()
    if not settings.hosted_workspace or os.getuid() != 10001 or os.getgid() != 10001:
        raise RuntimeError("Hosted serving requires the unprivileged workspace identity.")
    port = int(os.environ.get("PORT", "8000"))
    if not 1 <= port <= 65_535:
        raise ValueError("Invalid application port.")
    logging.getLogger(__name__).info(
        "hosted_startup_ready automatic_dispatch=%s", settings.local_email_automation_enabled
    )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
