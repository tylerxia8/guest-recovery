from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.errors import PermanentFailure, ProviderUnavailable
from app.adapters.reservations.mock import MOCK_TEST_SIGNATURE
from app.adapters.types import DeliveryUpdate, SendResult, VerifiedSmsEvent

_OUTCOME = "accepted"

_KNOWN_STATUS = {
    "queued",
    "accepted",
    "sent",
    "delivered",
    "failed",
    "undelivered",
    "unknown",
}


def set_mock_sms_outcome(outcome: str) -> None:
    global _OUTCOME
    if outcome not in {"accepted", "ambiguous", "undeliverable", "transient"}:
        raise ValueError("mock SMS outcome is not supported")
    _OUTCOME = outcome


def reset_mock_sms_outcome() -> None:
    global _OUTCOME
    _OUTCOME = "accepted"


@dataclass
class RecordingSmsGateway:
    """Records synthetic send intents and never contacts a carrier or Twilio."""

    sent: list[dict[str, str]] = field(default_factory=list)

    def send(self, intent_id: str, to: str, body: str, callback_url: str) -> SendResult:
        if _OUTCOME == "transient":
            raise ProviderUnavailable("mock recorder reported a pre-acceptance timeout")
        if _OUTCOME == "undeliverable":
            raise PermanentFailure("mock recorder marked the intent undeliverable")
        self.sent.append(
            {
                "intent_id": intent_id,
                "to": to,
                "body": body,
                "callback_url": callback_url,
            }
        )
        if _OUTCOME == "ambiguous":
            return SendResult(
                provider_message_id=f"mock-ambiguous-{intent_id}",
                accepted=False,
                ambiguous=True,
            )
        return SendResult(
            provider_message_id=f"mock-{intent_id}",
            accepted=True,
            ambiguous=False,
        )

    def verify_inbound(self, raw_bytes: bytes, headers: dict[str, str], public_url: str) -> VerifiedSmsEvent:
        del public_url
        signature = ""
        for key, value in headers.items():
            if key.lower() == "x-mock-signature":
                signature = value
        if signature != MOCK_TEST_SIGNATURE:
            raise PermanentFailure("mock SMS signature was rejected")
        fields: dict[str, str] = {}
        for line in raw_bytes.decode("utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        sender = fields.get("from", "")
        message_id = fields.get("message_id", "")
        body = fields.get("body", "")
        if not message_id.startswith("SM"):
            raise PermanentFailure("mock SMS message identity is missing")
        return VerifiedSmsEvent(provider="mock", message_id=message_id, sender=sender, body=body)

    def normalize_status(self, payload: dict[str, object]) -> DeliveryUpdate:
        message_id = str(payload.get("provider_message_id", ""))
        status = str(payload.get("status", "unknown"))
        if status not in _KNOWN_STATUS:
            status = "unknown"
        error_code = payload.get("error_code")
        return DeliveryUpdate(
            provider_message_id=message_id,
            status=status,
            error_code=str(error_code) if error_code else None,
        )
