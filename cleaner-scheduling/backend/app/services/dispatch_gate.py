from __future__ import annotations

from dataclasses import dataclass

from app.adapters.sms.mock import RecordingSmsGateway
from app.config import Settings


@dataclass(frozen=True)
class DispatchAttempt:
    sent: bool
    reasons: tuple[str, ...]
    gateway_calls: int


def evaluate_dispatch(settings: Settings, *, policy_approved: bool) -> tuple[str, ...]:
    """Return every reason live dispatch is refused. M1 never permits a send."""
    reasons: list[str] = []
    if not settings.production_dispatch_enabled:
        reasons.append("dispatch_disabled")
    if not settings.production_integrations_enabled:
        reasons.append("integrations_disabled")
    if (
        settings.sms_provider == "mock"
        or settings.reservation_provider == "mock"
        or settings.routing_provider == "mock"
    ):
        reasons.append("mock_provider_isolated")
    if settings.policy_approval_required and not policy_approved:
        reasons.append("policy_not_approved")
    if not reasons:
        reasons.append("production_adapter_unavailable")
    return tuple(reasons)


def attempt_outbound(
    settings: Settings,
    *,
    policy_approved: bool,
    gateway: RecordingSmsGateway,
) -> DispatchAttempt:
    reasons = evaluate_dispatch(settings, policy_approved=policy_approved)
    return DispatchAttempt(sent=False, reasons=reasons, gateway_calls=len(gateway.sent))
