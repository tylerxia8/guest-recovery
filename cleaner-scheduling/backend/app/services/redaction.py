from __future__ import annotations

from typing import cast

_HIDDEN_KEYS = {
    "access_code",
    "account_number",
    "address",
    "starting_address",
    "amount_cents",
    "authorization",
    "compensation_snapshot",
    "effective_encrypted",
    "entry_code",
    "flat_rate_cents",
    "local_secret_ciphertext",
    "email",
    "app_password",
    "phone",
    "phone_e164",
    "rate_id",
    "rate_snapshot",
    "rate_version",
    "routing_number",
    "rule_version",
    "source_secret_ciphertext",
    "token",
    "total_cents",
}

_STORAGE_HIDDEN_KEYS = _HIDDEN_KEYS - {
    "amount_cents",
    "compensation_snapshot",
    "flat_rate_cents",
    "rate_id",
    "rate_snapshot",
    "rate_version",
    "rule_version",
    "total_cents",
}


def redact_for_viewer(value: object) -> object:
    """Drop monetary and instruction fields. Viewers must not receive them from the API."""
    return _redact(value, _HIDDEN_KEYS)


def redact_for_storage(value: object) -> object:
    """Drop credentials and contact secrets before an idempotent response is stored."""
    return _redact(value, _STORAGE_HIDDEN_KEYS)


def _redact(value: object, hidden: set[str]) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        cleaned: dict[object, object] = {}
        for key, item in mapping.items():
            if isinstance(key, str) and key in hidden:
                continue
            cleaned[key] = _redact(item, hidden)
        return cleaned
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_redact(item, hidden) for item in items]
    return value
