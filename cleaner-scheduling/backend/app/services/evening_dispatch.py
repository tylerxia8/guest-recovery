"""Evening recording seam for isolated mocks. No production gateway is available."""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.services.sms_lifecycle import drain_mock_sms, enqueue_schedule_messages


def record_evening_schedules(session: Session, *, clock: Clock, settings: Settings) -> dict[str, object]:
    local = clock.now().astimezone(ZoneInfo("America/New_York"))
    if local.time() < time.fromisoformat(settings.dispatch_local_time):
        return {"due": False, "queued": 0, "recorded": 0, "external_send": False}
    day = local.date() + timedelta(days=1)
    # Version/part intent keys and the caller's transaction lock make retries safe.
    queued = enqueue_schedule_messages(session, clock=clock, settings=settings, service_date=day)
    recorded = drain_mock_sms(session, clock=clock, settings=settings)
    return {"due": True, "service_date": day.isoformat(), **queued, **recorded}
