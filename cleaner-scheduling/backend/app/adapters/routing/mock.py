from __future__ import annotations

import zlib
from datetime import UTC, datetime, timedelta

from app.adapters.errors import PermanentFailure
from app.adapters.types import Coordinates, Matrix

_FIXED_SECONDS = {
    ("P-A", "P-B"): 1200,
    ("P-B", "P-A"): 1800,
}
_OVERRIDES: dict[tuple[str, str], int | None] = {}


def set_travel_overrides(pairs: dict[tuple[str, str], int | None]) -> None:
    _OVERRIDES.clear()
    _OVERRIDES.update(pairs)


def clear_travel_overrides() -> None:
    _OVERRIDES.clear()


class MockRoutingService:
    """Deterministic travel times, including asymmetric and unreachable edges."""

    def matrix(self, points: tuple[str, ...], departure_bucket: str, mode: str) -> Matrix:
        if not points:
            raise PermanentFailure("routing matrix requires at least one point")
        observed = datetime.now(UTC)
        rows: list[tuple[int | None, ...]] = []
        for origin in points:
            row: list[int | None] = []
            for dest in points:
                row.append(_seconds(origin, dest, departure_bucket))
            rows.append(tuple(row))
        return Matrix(
            seconds_or_unreachable=tuple(rows),
            observed_at=observed,
            expires_at=observed + timedelta(minutes=60),
            provider="mock",
            provenance={"mode": mode, "departure_bucket": departure_bucket, "source": "mock"},
        )

    def geocode(self, authorized_location: str) -> Coordinates:
        if not authorized_location.startswith("synthetic:"):
            raise PermanentFailure("mock geocoder accepts only synthetic: locations")
        digest = zlib.crc32(authorized_location.encode("utf-8"))
        latitude = round((digest % 1000) / 100, 6)
        longitude = round(-((digest // 1000) % 1000) / 100, 6)
        return Coordinates(
            latitude=latitude,
            longitude=longitude,
            confidence="mock",
            provenance={"source": "mock"},
        )


def _seconds(origin: str, dest: str, bucket: str) -> int | None:
    if (origin, dest) in _OVERRIDES:
        return _OVERRIDES[(origin, dest)]
    if origin == dest:
        return 0
    origin_lower, dest_lower = origin.lower(), dest.lower()
    if (
        "unreachable" in origin_lower
        or "unreachable" in dest_lower
        or "missing" in origin_lower
        or "missing" in dest_lower
    ):
        return None
    fixed = _FIXED_SECONDS.get((origin, dest))
    if fixed is not None:
        return fixed
    return zlib.crc32(f"{origin}>{dest}|{bucket}".encode()) % 3000 + 60
