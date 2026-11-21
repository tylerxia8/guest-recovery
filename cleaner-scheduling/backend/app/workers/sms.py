from __future__ import annotations

from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.services.sms_lifecycle import drain_mock_sms, run_ack_timers


def sms_timer_tick(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    """Injected-clock reminder/escalation pass. It is not registered on Celery beat."""
    return run_ack_timers(session, clock=clock, settings=settings)


def sms_drain_tick(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    """Records queued mock messages. It does not contact a carrier."""
    return drain_mock_sms(session, clock=clock, settings=settings)
