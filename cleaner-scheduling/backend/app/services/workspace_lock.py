"""Workspace-scoped advisory locks for concurrent schedule and email operations.

Advisory locks are per-workspace to prevent blocking across unrelated tenants.
Locks must not be held across long-running I/O (solver, SMTP, external APIs).
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


def get_workspace_id(session: Session) -> uuid.UUID:
    """Get workspace_id from session.info.

    Raises ValueError if workspace_id is missing or invalid. Callers must
    ensure the session was created with a workspace_id or use
    LEGACY_WORKSPACE_ID explicitly when truly operating in legacy mode.
    """
    workspace_id = session.info.get("workspace_id")
    if workspace_id is None:
        raise ValueError(
            "Session missing workspace_id. "
            "Use session_factory(settings, workspace_id=...) or pass LEGACY_WORKSPACE_ID explicitly."
        )
    if not isinstance(workspace_id, uuid.UUID):
        raise ValueError(f"Session workspace_id must be a UUID, got {type(workspace_id).__name__}")
    return workspace_id


def _lock_hash(workspace_id: uuid.UUID, lock_name: str) -> str:
    """Generate a consistent lock key hash for a workspace-scoped lock."""
    return f"{workspace_id}:{lock_name}"


def acquire_workspace_lock(session: Session, workspace_id: uuid.UUID, lock_name: str) -> None:
    """Acquire a blocking workspace-scoped advisory lock.

    The lock is held for the duration of the current transaction.
    Use this when the caller must succeed and can wait.

    Args:
        session: SQLAlchemy session with an active transaction
        workspace_id: UUID of the workspace to scope the lock to
        lock_name: Descriptive name of the lock (e.g., 'schedule-publication')
    """
    key = _lock_hash(workspace_id, lock_name)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    logger.debug("workspace_lock.acquired workspace_id=%s lock=%s", workspace_id, lock_name)


def try_acquire_workspace_lock(session: Session, workspace_id: uuid.UUID, lock_name: str) -> bool:
    """Try to acquire a workspace-scoped advisory lock without blocking.

    Returns immediately with True if the lock was acquired, False otherwise.
    Use this when the caller can skip or retry if another process holds the lock.

    Args:
        session: SQLAlchemy session with an active transaction
        workspace_id: UUID of the workspace to scope the lock to
        lock_name: Descriptive name of the lock (e.g., 'schedule-publication')

    Returns:
        True if lock was acquired, False if already held by another session
    """
    key = _lock_hash(workspace_id, lock_name)
    result = session.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}).scalar()
    acquired = bool(result)
    if acquired:
        logger.debug("workspace_lock.try_acquired workspace_id=%s lock=%s", workspace_id, lock_name)
    else:
        logger.debug("workspace_lock.try_failed workspace_id=%s lock=%s", workspace_id, lock_name)
    return acquired


@contextmanager
def workspace_lock(session: Session, workspace_id: uuid.UUID, lock_name: str) -> Iterator[None]:
    """Context manager for workspace-scoped advisory lock.

    Acquires the lock and releases it when the context exits.
    The lock is transaction-scoped, so it's released on commit/rollback.

    Example:
        with workspace_lock(session, workspace_id, "schedule-publication"):
            # Critical section - no long I/O here
            pass
    """
    acquire_workspace_lock(session, workspace_id, lock_name)
    yield


SCHEDULE_PUBLICATION_LOCK = "schedule-publication"
EMAIL_AUTOMATION_LOCK = "email-automation"
AVAILABILITY_LOCK = "availability"
