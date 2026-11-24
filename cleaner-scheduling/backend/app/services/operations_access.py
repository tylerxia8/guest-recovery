"""Short-lived access to selected Cleaner operations for linked Lucena tools."""

import re
import secrets

import redis
from redis.exceptions import RedisError

from app.domain.errors import AppError
from app.services.workspace_accounts import token_digest

STAY_CONTEXT_PATH = "/v1/workspace/operations/stay-context"
STAYS_PATH = "/v1/workspace/operations/stays"
MESSAGES_PATH = "/v1/workspace/operations/guest-messages"
OPERATIONS_READ_PATHS = frozenset({STAY_CONTEXT_PATH, STAYS_PATH, MESSAGES_PATH})
REPAIR_CONTEXT_PATH = "/v1/workspace/operations/repair-context"
TOUCHUP_PATH = "/v1/workspace/operations/cleaning-touchup"
ACCESS_SECONDS = 900


def issue_read_access(cache: redis.Redis, session_digest: str) -> str:
    return issue_access(cache, session_digest, "ops_")


def issue_touchup_access(cache: redis.Redis, session_digest: str) -> str:
    return issue_access(cache, session_digest, "opc_")


def issue_repair_access(cache: redis.Redis, session_digest: str) -> str:
    return issue_access(cache, session_digest, "opr_")


def issue_access(cache: redis.Redis, session_digest: str, prefix: str, *, ttl: int = ACCESS_SECONDS) -> str:
    token = prefix + secrets.token_urlsafe(32)
    try:
        saved = cache.set(f"operations-access:{token_digest(token)}", session_digest, ex=ttl, nx=True)
    except RedisError as exc:
        raise AppError("dependency_unavailable", "Shared workspace access is temporarily unavailable.", 503) from exc
    if not saved:
        raise AppError("dependency_unavailable", "Shared workspace access is temporarily unavailable.", 503)
    return token


def read_session_digest(cache: redis.Redis, token: str) -> str:
    if re.fullmatch(r"(?:ops|opc|opr|opm)_[A-Za-z0-9_-]{43}", token) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    try:
        value = cache.get(f"operations-access:{token_digest(token)}")
    except RedisError as exc:
        raise AppError("dependency_unavailable", "Shared workspace access is temporarily unavailable.", 503) from exc
    if isinstance(value, bytes):
        value = value.decode("ascii")
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise AppError("invalid_session", "Sign in to continue.", 401)
    return value
