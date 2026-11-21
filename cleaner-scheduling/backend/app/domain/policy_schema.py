from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_BEDROOM_KEYS = {"0", "1", "2", "3", "4"}


def _minutes(value: str) -> int:
    if not _TIME.fullmatch(value):
        raise ValueError("time must be HH:MM in 24-hour form")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class PolicySettings(BaseModel):
    """Versioned operational defaults. This object cannot enable transfers or live dispatch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    availability_request_local_time: str
    availability_cutoff_local_time: str
    dispatch_local_time: str
    ack_reminder_minutes: int = Field(ge=1, le=24 * 60)
    ack_escalation_minutes: int = Field(ge=1, le=24 * 60)
    readiness_buffer_minutes: int = Field(ge=1, le=240)
    auto_reassign_missing_ack: Literal[False]
    pre_arrival_horizon_hours: int = Field(ge=1, le=168)
    duration_minutes_by_bedrooms: dict[str, int]
    solver_time_limit_seconds: int = Field(ge=1, le=120)
    reservation_reconcile_minutes: int = Field(ge=1, le=1440)
    routing_cache_ttl_minutes: int = Field(ge=1, le=1440)
    integration_stale_threshold_minutes: int = Field(ge=1, le=1440)
    mandatory_return_to_base: Literal[False]

    @field_validator(
        "availability_request_local_time",
        "availability_cutoff_local_time",
        "dispatch_local_time",
    )
    @classmethod
    def _clock(cls, value: str) -> str:
        _minutes(value)
        return value

    @field_validator("duration_minutes_by_bedrooms")
    @classmethod
    def _durations(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != _BEDROOM_KEYS:
            raise ValueError("duration map must contain bedrooms 0 through 4 and must not include 5+")
        for key, minutes in value.items():
            if minutes < 1:
                raise ValueError(f"duration for {key} bedrooms must be positive")
        return value

    @model_validator(mode="after")
    def _order(self) -> PolicySettings:
        request = _minutes(self.availability_request_local_time)
        cutoff = _minutes(self.availability_cutoff_local_time)
        dispatch = _minutes(self.dispatch_local_time)
        if not request <= cutoff <= dispatch:
            raise ValueError("availability request, cutoff, and dispatch times must be in that order")
        if self.ack_escalation_minutes <= self.ack_reminder_minutes:
            raise ValueError("acknowledgment escalation must be later than the reminder")
        return self


class CreatePolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: PolicySettings
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class ApprovePolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_note: str = Field(min_length=1, max_length=500)
    effective_at: datetime

    @field_validator("approval_note")
    @classmethod
    def _note(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("approval note must not be blank")
        return stripped

    @field_validator("effective_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("effective_at must include a timezone offset")
        return value
