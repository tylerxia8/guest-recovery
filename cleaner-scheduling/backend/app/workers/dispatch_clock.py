from __future__ import annotations

from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.services.schedules import run_dispatch_clock


def dispatch_clock_tick(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    """Injected-clock entry point. It is not registered on Celery beat."""
    return run_dispatch_clock(session, clock=clock, settings=settings)
