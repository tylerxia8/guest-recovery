from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from app.api.app_factory import create_app
from app.config import get_settings
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def test_openapi_lists_foundation_and_ingestion_routes(client: TestClient) -> None:
    schema = client.app.openapi()
    assert set(schema["paths"]) == {
        "/v1/accounts/signup",
        "/v1/accounts/login",
        "/v1/accounts/logout",
        "/v1/accounts/me",
        "/v1/workspace/setup",
        "/v1/workspace/hospitable",
        "/v1/workspace/cleaners",
        "/v1/workspace/complete",
        "/v1/workspace/onboarding-emails",
        "/v1/cleaner-portal/language",
        "/v1/assignments/{assignment_id}/lock",
        "/v1/assignments/{assignment_id}/override",
        "/v1/calendar-feeds/check",
        "/v1/cleaner-links/{token}",
        "/v1/cleaner-onboarding",
        "/v1/cleaner-portal",
        "/v1/cleaners",
        "/v1/cleaners/{cleaner_id}/availability",
        "/v1/cleaners/{cleaner_id}/consent",
        "/v1/cleaners/{cleaner_id}/eligibility",
        "/v1/cleaners/{cleaner_id}/email",
        "/v1/cleaners/{cleaner_id}/languages",
        "/v1/cleaners/{cleaner_id}/links",
        "/v1/cleaners/{cleaner_id}/onboarding-link",
        "/v1/cleaners/{cleaner_id}/remove",
        "/v1/cleaners/{cleaner_id}/weekly-hours",
        "/v1/cleaners/{cleaner_id}/workday",
        "/v1/daily-operations",
        "/v1/dashboard",
        "/v1/email/assignments",
        "/v1/email/assignments/{cleaner_id}",
        "/v1/email/portal/{cleaner_id}",
        "/v1/email/sample-assignment",
        "/v1/email/status",
        "/v1/email/test",
        "/v1/exceptions",
        "/v1/exceptions/{exception_id}",
        "/v1/health/live",
        "/v1/health/ready",
        "/v1/jobs/{job_id}",
        "/v1/local-scheduling/policy",
        "/v1/locations",
        "/v1/policy-versions",
        "/v1/policy-versions/{policy_id}/approve",
        "/v1/properties",
        "/v1/properties/{property_id}",
        "/v1/properties/{property_id}/instructions",
        "/v1/properties/{property_id}/instructions/source",
        "/v1/properties/{property_id}/instructions/versions",
        "/v1/reservation-imports/preview",
        "/v1/reservation-imports/{import_id}/commit",
        "/v1/reservation-syncs",
        "/v1/reservation-syncs/{sync_id}",
        "/v1/reservations",
        "/v1/schedules",
        "/v1/schedules/previews",
        "/v1/schedules/runs/{run_id}",
        "/v1/schedules/runs/{run_id}/publish",
        "/v1/setup",
        "/v1/sms-messages",
        "/v1/tasks",
        "/v1/tasks/{task_id}/target",
        "/v1/webhooks/hospitable",
        "/v1/webhooks/twilio/inbound",
        "/v1/webhooks/twilio/status",
    }


def test_frontend_source_has_no_provider_credentials() -> None:
    frontend = REPO / "frontend"
    banned = ("TWILIO_", "AIza", "BEGIN PRIVATE", "sk_live", "HOSPITABLE_CREDENTIAL")
    for path in frontend.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts or path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in banned:
            assert marker not in text, f"{marker} found in {path}"


def test_uvicorn_health_smoke() -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 30
        live = None
        while time.time() < deadline:
            if process.poll() is not None:
                raise AssertionError("uvicorn exited before readiness")
            try:
                live = httpx.get("http://127.0.0.1:8765/v1/health/live", timeout=1)
                if live.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        assert live is not None and live.status_code == 200
        ready = httpx.get("http://127.0.0.1:8765/v1/health/ready", timeout=2)
        assert ready.status_code == 200
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def test_celery_worker_boots_without_a_schedule(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    log_path = tmp_path / "celery-smoke.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.workers.celery_app",
                "worker",
                "--loglevel=INFO",
                "--pool=solo",
                "--concurrency=1",
            ],
            cwd=BACKEND,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            if process.poll() is not None:
                raise AssertionError(log_path.read_text(encoding="utf-8"))
            contents = log_path.read_text(encoding="utf-8").lower()
            if "ready" in contents:
                ready = True
                break
            time.sleep(0.5)
        assert ready, log_path.read_text(encoding="utf-8")
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        log_path.unlink(missing_ok=True)


def test_create_app_uses_injected_settings() -> None:
    application = create_app(get_settings())
    assert application.title == "Cleaning coordination API"
