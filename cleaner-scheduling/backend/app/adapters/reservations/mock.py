from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.adapters.errors import PermanentFailure, ProviderUnavailable
from app.adapters.types import (
    NormalizedReservation,
    ReservationCapabilities,
    ReservationPage,
    VerifiedEnvelope,
)

MOCK_TEST_SIGNATURE = "mock-test-v1"
_GUEST_KEYS = {"guest", "guest_email", "guest_name", "guest_phone", "guest_payment"}


@dataclass
class MockFeed:
    pages: list[list[dict[str, object]]] = field(default_factory=list)
    fail_at_page: int | None = None
    outage: bool = False
    complete: bool = True
    snapshot_id: str = "mock-snapshot"
    observed_at: datetime | None = None


_feed = MockFeed()


def reset_mock_feed() -> None:
    global _feed
    _feed = MockFeed()


def set_mock_feed(feed: MockFeed) -> None:
    global _feed
    _feed = feed


class MockReservationProvider:
    """Deterministic fixture provider. It does not call Hospitable or open a socket."""

    def capabilities(self) -> ReservationCapabilities:
        return ReservationCapabilities(
            incremental=True,
            full_snapshot=True,
            webhooks=True,
            cancellations=True,
            actual_times=True,
            instructions=True,
        )

    def pull(self, cursor: str | None = None, since: datetime | None = None) -> ReservationPage:
        del since
        if _feed.outage:
            raise ProviderUnavailable("mock reservation provider is unavailable")
        if cursor is not None and cursor.startswith("end:"):
            observed = _feed.observed_at or datetime.now(UTC)
            return ReservationPage(
                records=(),
                next_cursor=None,
                snapshot_id=cursor.removeprefix("end:"),
                complete=True,
                source_as_of=observed,
            )
        index = 0 if cursor is None else int(cursor)
        if _feed.fail_at_page is not None and index == _feed.fail_at_page:
            raise ProviderUnavailable("mock page failed before completion")
        if not _feed.pages:
            observed = _feed.observed_at or datetime.now(UTC)
            return ReservationPage(
                records=(),
                next_cursor=None,
                snapshot_id="mock-empty",
                complete=True,
                source_as_of=observed,
            )
        if index >= len(_feed.pages):
            raise PermanentFailure("mock cursor is outside the fixture")
        records: list[NormalizedReservation] = []
        rejections: list[tuple[str, str]] = []
        observed = _feed.observed_at or datetime.now(UTC)
        for raw in _feed.pages[index]:
            try:
                records.extend(self.normalize_event(raw))
            except PermanentFailure as exc:
                external = str(raw.get("external_id", "unknown"))
                rejections.append((_rejection_code(str(exc)), external))
        last = index + 1 >= len(_feed.pages)
        return ReservationPage(
            records=tuple(records),
            next_cursor=None if last else str(index + 1),
            snapshot_id=_feed.snapshot_id,
            complete=bool(_feed.complete and last),
            source_as_of=observed,
            rejections=tuple(rejections),
        )

    def normalize_event(self, raw: dict[str, object]) -> tuple[NormalizedReservation, ...]:
        if _GUEST_KEYS.intersection(raw):
            raise PermanentFailure("guest data is not accepted")
        required = (
            "provider",
            "external_id",
            "property_external_id",
            "status",
            "checkin_at",
            "checkout_at",
        )
        if any(key not in raw for key in required):
            raise PermanentFailure("reservation event is missing required canonical fields")
        status = str(raw["status"])
        if status not in {"active", "canceled"}:
            raise PermanentFailure("reservation status is not supported")
        checkin = _parse_time(raw["checkin_at"])
        checkout = _parse_time(raw["checkout_at"])
        if checkout <= checkin:
            raise PermanentFailure("checkout must be after check-in")
        source_updated = raw.get("source_updated_at")
        observed = datetime.now(UTC)
        return (
            NormalizedReservation(
                provider=str(raw["provider"]),
                external_id=str(raw["external_id"]),
                property_external_id=str(raw["property_external_id"]),
                status=status,
                checkin_at=checkin,
                checkout_at=checkout,
                source_updated_at=_parse_time(source_updated) if source_updated else None,
                source_revision=str(raw["source_revision"]) if raw.get("source_revision") else None,
                timestamp_provenance=str(raw.get("timestamp_provenance") or "mock-canonical"),
                observed_at=observed,
            ),
        )

    def verify_webhook(self, raw_bytes: bytes, headers: dict[str, str], public_url: str) -> VerifiedEnvelope:
        del public_url
        signature = _header(headers, "x-mock-signature")
        if signature != MOCK_TEST_SIGNATURE:
            raise PermanentFailure("mock webhook signature was rejected")
        if not raw_bytes:
            raise PermanentFailure("mock webhook body is empty")
        return VerifiedEnvelope(provider="mock", dedupe_key=_digest_key(raw_bytes), changes=())


def _digest_key(raw: bytes) -> str:
    import hashlib

    return "mock:" + hashlib.sha256(raw).hexdigest()


def _rejection_code(message: str) -> str:
    if "guest" in message:
        return "guest_data_rejected"
    if "timestamp" in message or "offset" in message:
        return "invalid_timestamp"
    if "status" in message:
        return "unsupported_status"
    if "interval" in message or "checkout" in message:
        return "invalid_interval"
    return "invalid_reservation"


def _header(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise PermanentFailure("reservation timestamps must be strings")
    if "T" not in value:
        raise PermanentFailure("date-only timestamp is not an approved interpretation")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermanentFailure("reservation timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PermanentFailure("reservation timestamp must include an offset")
    return parsed.astimezone(UTC)
