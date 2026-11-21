from __future__ import annotations

from app.adapters.sms.mock import RecordingSmsGateway
from app.adapters.types import SendResult
from app.config import get_settings
from app.services.dispatch_gate import attempt_outbound
from app.workers.celery_app import celery_app
from app.workers.tasks import dispatch_tick
from tests.test_config import base_settings


class ExplodingGateway(RecordingSmsGateway):
    def send(self, intent_id: str, to: str, body: str, callback_url: str) -> SendResult:
        raise AssertionError("SMS send must not be called")


def test_default_flags_do_not_send() -> None:
    attempt = attempt_outbound(get_settings(), policy_approved=False, gateway=ExplodingGateway())
    assert attempt.sent is False
    assert attempt.gateway_calls == 0
    assert "dispatch_disabled" in attempt.reasons
    assert "integrations_disabled" in attempt.reasons
    assert "mock_provider_isolated" in attempt.reasons
    assert "policy_not_approved" in attempt.reasons


def test_enabled_flags_with_mocks_and_unapproved_policy_do_not_send() -> None:
    settings = base_settings(
        production_integrations_enabled=True,
        production_dispatch_enabled=True,
    )
    attempt = attempt_outbound(settings, policy_approved=False, gateway=ExplodingGateway())
    assert attempt.sent is False
    assert "policy_not_approved" in attempt.reasons
    assert "mock_provider_isolated" in attempt.reasons
    assert "dispatch_disabled" not in attempt.reasons


def test_flags_and_approval_still_refuse_unimplemented_production_send() -> None:
    settings = base_settings(
        production_integrations_enabled=True,
        production_dispatch_enabled=True,
        policy_approval_required=True,
        reservation_provider="hospitable",
        sms_provider="twilio",
        routing_provider="google",
    )
    attempt = attempt_outbound(settings, policy_approved=True, gateway=ExplodingGateway())
    assert attempt.sent is False
    assert attempt.reasons == ("production_adapter_unavailable",)


def test_scheduler_is_unscheduled_and_does_not_send() -> None:
    assert celery_app.conf.beat_schedule == {}
    result = dispatch_tick()
    assert result["sent"] is False
    assert result["gateway_calls"] == 0
