from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC instant."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("clock instant must be timezone-aware")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant
