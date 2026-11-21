from __future__ import annotations

import uuid

from app.clock import SystemClock
from app.config import get_settings
from app.db import session_factory
from app.services.reservation_sync import execute_sync
from app.workers.celery_app import celery_app
from app.workspaces import LEGACY_WORKSPACE_ID


def run_reservation_sync(sync_run_id: str) -> str:
    """Replay a queued mock sync. Not placed on the beat schedule."""
    settings = get_settings()
    factory = session_factory(settings, LEGACY_WORKSPACE_ID)
    with factory() as session:
        run = execute_sync(session, clock=SystemClock(), sync_run_id=uuid.UUID(sync_run_id))
        status = run.status
        session.commit()
    return status


run_reservation_sync = celery_app.task(name="foundation.reservation_sync")(run_reservation_sync)
