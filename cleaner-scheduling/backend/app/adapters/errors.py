from __future__ import annotations


class AdapterError(Exception):
    code = "adapter_error"


class CapabilityUnavailable(AdapterError):
    code = "capability_unavailable"

    def __init__(self, capability: str, message: str) -> None:
        super().__init__(message)
        self.capability = capability


class ProviderUnavailable(AdapterError):
    code = "provider_unavailable"


class RateLimited(AdapterError):
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int, message: str) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentFailure(AdapterError):
    code = "permanent_failure"


class AmbiguousOutcome(AdapterError):
    code = "ambiguous_outcome"
