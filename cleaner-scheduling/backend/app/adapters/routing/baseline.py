"""An immutable, offline driving baseline. No network or synthetic fallback."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.adapters.types import Matrix


class PropertyBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: str
    name: str


class DrivingBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    captured_at: datetime
    source_url: str
    coordinate_source_url: str
    staging_name: str
    staging_source_url: str
    points: list[str]
    seconds: list[list[StrictInt | None]]
    origin_aliases: dict[str, str]
    property_bindings: dict[str, PropertyBinding]
    coordinates: dict[str, list[float]]
    max_snap_distance_meters: float

    @model_validator(mode="after")
    def validate_matrix(self) -> DrivingBaseline:
        size = len(self.points)
        if not size or len(set(self.points)) != size or len(self.seconds) != size:
            raise ValueError("Invalid baseline dimensions or duplicate point")
        if self.captured_at.tzinfo is None:
            raise ValueError("Baseline capture time must include timezone")
        for i, row in enumerate(self.seconds):
            if len(row) != size or row[i] != 0:
                raise ValueError("Invalid baseline row or diagonal")
            if any(value is not None and (isinstance(value, bool) or value < 0) for value in row):
                raise ValueError("Invalid baseline duration")
        if any(target not in self.points for target in self.origin_aliases.values()):
            raise ValueError("Unknown origin alias target")
        if any(key not in self.points for key in self.property_bindings):
            raise ValueError("Unknown property binding")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def matrix(self, points: tuple[str, ...]) -> Matrix:
        indices = {key: i for i, key in enumerate(self.points)}
        keys = [self.origin_aliases.get(key, key) for key in points]
        rows = tuple(
            tuple(self.seconds[indices[a]][indices[b]] if a in indices and b in indices else None for b in keys)
            for a in keys
        )
        return Matrix(
            seconds_or_unreachable=rows,
            observed_at=self.captured_at,
            # Existing storage requires expiry > observation. Mark the cache expired
            # immediately; this explicitly selected static baseline is not live routing.
            expires_at=self.captured_at + timedelta(microseconds=1),
            provider="osrm_cached",
            provenance={"baseline_id": self.digest, "source": self.source_url, "traffic": "none"},
        )

    def summary(self) -> dict[str, object]:
        return {
            "mode": "saved_driving_baseline",
            "baseline_id": self.digest,
            "captured_at": self.captured_at.isoformat(),
            "source_url": self.source_url,
            "property_count": len(self.property_bindings),
            "directed_property_pairs": len(self.property_bindings) * (len(self.property_bindings) - 1),
            "unreachable_pairs": sum(value is None for row in self.seconds for value in row),
            "staging_name": self.staging_name,
            "staging_source_url": self.staging_source_url,
            "staging_origin_label": next(
                (key for key, target in self.origin_aliases.items() if target == "demo:public-staging"), None
            ),
            "max_snap_distance_meters": self.max_snap_distance_meters,
            "traffic": "none",
        }
