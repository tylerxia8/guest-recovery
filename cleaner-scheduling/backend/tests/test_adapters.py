from __future__ import annotations

import socket
from pathlib import Path

import pytest
from app.adapters.errors import CapabilityUnavailable, PermanentFailure
from app.adapters.factory import build_routing_service, build_sms_gateway
from app.adapters.reservations.hospitable import HospitableReservationProvider
from app.adapters.reservations.mock import MOCK_TEST_SIGNATURE, MockReservationProvider
from app.adapters.routing.mock import MockRoutingService
from app.adapters.sms.mock import RecordingSmsGateway
from app.domain.errors import ConfigError


def test_recording_sms_does_not_open_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "create_connection", fail)
    result = RecordingSmsGateway().send(
        "intent-1",
        "+17045550100",
        "synthetic body",
        "https://example.invalid/callback",
    )
    assert result.accepted is True
    assert result.ambiguous is False
    assert result.provider_message_id == "mock-intent-1"


def test_twilio_and_google_factories_refuse_live_clients() -> None:
    with pytest.raises(ConfigError) as sms_error:
        build_sms_gateway("twilio")
    with pytest.raises(ConfigError) as routing_error:
        build_routing_service("google")
    assert sms_error.value.code == "production_adapter_unavailable"
    assert routing_error.value.code == "production_adapter_unavailable"


def test_mock_routing_is_asymmetric_and_unreachable_is_not_zero() -> None:
    matrix = MockRoutingService().matrix(("P-A", "P-B", "P-Z-UNREACHABLE"), "2026-11-10T15:00Z", "driving")
    assert matrix.seconds_or_unreachable[0][1] == 1200
    assert matrix.seconds_or_unreachable[1][0] == 1800
    assert matrix.seconds_or_unreachable[0][2] is None
    assert matrix.provider == "mock"
    with pytest.raises(PermanentFailure):
        MockRoutingService().geocode("123 Main Street")


def test_mock_reservations_reject_guest_data_and_bad_signatures() -> None:
    provider = MockReservationProvider()
    assert provider.capabilities().full_snapshot is True
    page = provider.pull()
    assert page.complete is True
    assert page.records == ()
    with pytest.raises(PermanentFailure):
        provider.normalize_event(
            {
                "provider": "mock",
                "external_id": "R-1",
                "property_external_id": "P-1",
                "status": "active",
                "checkin_at": "2026-11-09T16:00:00-05:00",
                "checkout_at": "2026-11-10T10:00:00-05:00",
                "guest_name": "Synthetic Guest",
            }
        )
    with pytest.raises(PermanentFailure):
        provider.verify_webhook(b"{}", {}, "https://example.invalid/hook")
    envelope = provider.verify_webhook(
        b"synthetic-event",
        {"X-Mock-Signature": MOCK_TEST_SIGNATURE},
        "https://example.invalid/hook",
    )
    assert envelope.provider == "mock"


def test_hospitable_stub_has_no_invented_signature_scheme() -> None:
    source = Path("app/adapters/reservations/hospitable.py").read_text(encoding="utf-8").lower()
    assert "hmac" not in source
    assert "sha256" not in source
    assert "api.hospitable" not in source
    provider = HospitableReservationProvider()
    with pytest.raises(CapabilityUnavailable):
        provider.pull()
    with pytest.raises(CapabilityUnavailable):
        provider.verify_webhook(b"{}", {"X-Hospitable-Signature": "guess"}, "https://example.invalid")
