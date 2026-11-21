from __future__ import annotations

from datetime import datetime

from app.adapters.errors import CapabilityUnavailable
from app.adapters.types import (
    ReservationCapabilities,
    ReservationPage,
    VerifiedEnvelope,
)


class HospitableReservationProvider:
    """Disabled stub.

    This class is not a Hospitable client. It does not invent endpoints,
    authentication, webhook signatures, or payload schemas.
    """

    def _refuse(self) -> None:
        raise CapabilityUnavailable(
            "hospitable",
            "Hospitable API access is not verified. Production ingress stays disabled.",
        )

    def capabilities(self) -> ReservationCapabilities:
        self._refuse()
        raise AssertionError("unreachable")

    def pull(self, cursor: str | None = None, since: datetime | None = None) -> ReservationPage:
        del cursor, since
        self._refuse()
        raise AssertionError("unreachable")

    def normalize_event(self, raw: dict[str, object]) -> tuple[object, ...]:
        del raw
        self._refuse()
        raise AssertionError("unreachable")

    def verify_webhook(self, raw_bytes: bytes, headers: dict[str, str], public_url: str) -> VerifiedEnvelope:
        del raw_bytes, headers, public_url
        self._refuse()
        raise AssertionError("unreachable")
