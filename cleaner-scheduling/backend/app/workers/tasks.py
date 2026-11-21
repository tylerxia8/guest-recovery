from __future__ import annotations

from collections.abc import Callable
from typing import cast

from app.adapters.factory import build_sms_gateway
from app.config import get_settings
from app.db import session_factory
from app.services.dispatch_gate import attempt_outbound
from app.services.policy import active_policy_exists
from app.workers.celery_app import celery_app
from app.workspaces import LEGACY_WORKSPACE_ID


def dispatch_tick() -> dict[str, object]:
    """Manual seam for the future scheduler. It is not on the beat schedule and never sends."""
    settings = get_settings()
    factory = session_factory(settings, LEGACY_WORKSPACE_ID)
    with factory() as session:
        approved = active_policy_exists(session)
    gateway = build_sms_gateway(settings.sms_provider)
    attempt = attempt_outbound(settings, policy_approved=approved, gateway=gateway)
    return {
        "sent": attempt.sent,
        "reasons": list(attempt.reasons),
        "gateway_calls": attempt.gateway_calls,
    }


dispatch_tick = cast(
    Callable[[], dict[str, object]],
    celery_app.task(name="foundation.dispatch_tick")(dispatch_tick),
)
