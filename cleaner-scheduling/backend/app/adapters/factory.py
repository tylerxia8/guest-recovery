from __future__ import annotations

from app.adapters.reservations.hospitable import HospitableReservationProvider
from app.adapters.reservations.mock import MockReservationProvider
from app.adapters.routing.mock import MockRoutingService
from app.adapters.sms.mock import RecordingSmsGateway
from app.domain.errors import ConfigError


def build_reservation_provider(name: str) -> MockReservationProvider | HospitableReservationProvider:
    if name == "mock":
        return MockReservationProvider()
    if name == "hospitable":
        return HospitableReservationProvider()
    raise ConfigError("unsupported_provider", "reservation provider is not supported")


def build_sms_gateway(name: str) -> RecordingSmsGateway:
    if name == "mock":
        return RecordingSmsGateway()
    if name == "twilio":
        raise ConfigError(
            "production_adapter_unavailable",
            "Twilio adapter is not implemented. Refusing to construct a live client.",
        )
    raise ConfigError("unsupported_provider", "SMS provider is not supported")


def build_routing_service(name: str) -> MockRoutingService:
    if name == "mock":
        return MockRoutingService()
    if name == "google":
        raise ConfigError(
            "production_adapter_unavailable",
            "Google routing adapter is not implemented. Refusing to construct a live client.",
        )
    raise ConfigError("unsupported_provider", "routing provider is not supported")
