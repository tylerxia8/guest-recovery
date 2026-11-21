from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ReservationCapabilities:
    incremental: bool
    full_snapshot: bool
    webhooks: bool
    cancellations: bool
    actual_times: bool
    instructions: bool


@dataclass(frozen=True)
class NormalizedReservation:
    provider: str
    external_id: str
    property_external_id: str
    status: str
    checkin_at: datetime
    checkout_at: datetime
    source_updated_at: datetime | None
    source_revision: str | None
    timestamp_provenance: str
    observed_at: datetime


@dataclass(frozen=True)
class ReservationPage:
    records: tuple[NormalizedReservation, ...]
    next_cursor: str | None
    snapshot_id: str | None
    complete: bool
    source_as_of: datetime
    rejections: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class VerifiedEnvelope:
    provider: str
    dedupe_key: str
    changes: tuple[NormalizedReservation, ...]


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str | None
    accepted: bool
    ambiguous: bool


@dataclass(frozen=True)
class VerifiedSmsEvent:
    provider: str
    message_id: str
    sender: str
    body: str


@dataclass(frozen=True)
class DeliveryUpdate:
    provider_message_id: str
    status: str
    error_code: str | None


@dataclass(frozen=True)
class Matrix:
    seconds_or_unreachable: tuple[tuple[int | None, ...], ...]
    observed_at: datetime
    expires_at: datetime
    provider: str
    provenance: dict[str, str]


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float
    confidence: str
    provenance: dict[str, str]


class ReservationProvider(Protocol):
    def capabilities(self) -> ReservationCapabilities: ...

    def pull(self, cursor: str | None = None, since: datetime | None = None) -> ReservationPage: ...

    def normalize_event(self, raw: dict[str, object]) -> tuple[NormalizedReservation, ...]: ...

    def verify_webhook(self, raw_bytes: bytes, headers: dict[str, str], public_url: str) -> VerifiedEnvelope: ...


class SmsGateway(Protocol):
    def send(self, intent_id: str, to: str, body: str, callback_url: str) -> SendResult: ...

    def verify_inbound(self, raw_bytes: bytes, headers: dict[str, str], public_url: str) -> VerifiedSmsEvent: ...

    def normalize_status(self, payload: dict[str, object]) -> DeliveryUpdate: ...


class RoutingService(Protocol):
    def matrix(self, points: tuple[str, ...], departure_bucket: str, mode: str) -> Matrix: ...

    def geocode(self, authorized_location: str) -> Coordinates: ...
