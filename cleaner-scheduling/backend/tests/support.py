from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
from app.db import get_engine
from sqlalchemy import text

ISSUER = "http://localhost/mock-issuer"
AUDIENCE = "cleaning-local"
SECRET = "test-only-mock-secret-not-a-credential"


def valid_settings() -> dict[str, object]:
    return {
        "schema_version": 1,
        "availability_request_local_time": "17:00",
        "availability_cutoff_local_time": "17:30",
        "dispatch_local_time": "18:00",
        "ack_reminder_minutes": 30,
        "ack_escalation_minutes": 60,
        "readiness_buffer_minutes": 30,
        "auto_reassign_missing_ack": False,
        "pre_arrival_horizon_hours": 48,
        "duration_minutes_by_bedrooms": {"0": 60, "1": 90, "2": 120, "3": 150, "4": 180},
        "solver_time_limit_seconds": 30,
        "reservation_reconcile_minutes": 15,
        "routing_cache_ttl_minutes": 60,
        "integration_stale_threshold_minutes": 30,
        "mandatory_return_to_base": False,
    }


def make_token(
    subject: str,
    *,
    roles: tuple[str, ...] = ("owner",),
    expired: bool = False,
    secret: str = SECRET,
) -> str:
    now = datetime.now(UTC)
    expires = now - timedelta(minutes=5) if expired else now + timedelta(minutes=10)
    encoded = jwt.encode(
        {
            "sub": subject,
            "roles": list(roles),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": expires,
        },
        secret,
        algorithm="HS256",
    )
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def auth_headers(subject: str, *, key: str = "key-1", **token_kwargs: object) -> dict[str, str]:
    roles = token_kwargs.get("roles", ("owner",))
    expired = bool(token_kwargs.get("expired", False))
    secret = str(token_kwargs.get("secret", SECRET))
    if not isinstance(roles, tuple):
        raise TypeError("roles must be a tuple")
    return {
        "Authorization": f"Bearer {make_token(subject, roles=roles, expired=expired, secret=secret)}",
        "Idempotency-Key": key,
    }


def count_rows(table: str) -> int:
    if table not in {
        "idempotency_records",
        "outbox_events",
        "webhook_events",
        "audit_logs",
        "policy_versions",
        "operator_profiles",
        "properties",
        "reservations",
        "reservation_sync_runs",
        "reservation_import_previews",
        "property_instructions",
        "exceptions",
        "cleaning_tasks",
    }:
        raise ValueError("unexpected table")
    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        value = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    return int(value)


def audit_blob() -> str:
    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_type, actor_subject, action, reason, redacted_changes::text, "
                "coalesce(request_id, '') FROM audit_logs"
            )
        ).all()
    return "\n".join(str(row) for row in rows)
