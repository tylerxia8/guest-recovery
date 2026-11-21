from __future__ import annotations

import csv
import io
from datetime import datetime

_HEADER = (
    "provider",
    "external_id",
    "property_external_id",
    "status",
    "checkin_at",
    "checkout_at",
    "source_revision",
    "source_updated_at",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")
MAX_ROWS = 10_000
MAX_BYTES = 5 * 1024 * 1024


def parse_reservation_csv(payload: bytes) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if len(payload) > MAX_BYTES:
        return [], [{"row": 0, "code": "file_too_large"}]
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        return [], [{"row": 0, "code": "not_utf8"}]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or tuple(reader.fieldnames) != _HEADER:
        return [], [{"row": 1, "code": "malformed_schema"}]
    valid: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    seen: dict[str, dict[str, object]] = {}
    for index, row in enumerate(reader, start=2):
        if index - 1 > MAX_ROWS:
            errors.append({"row": index, "code": "too_many_rows"})
            break
        code = _validate_row(row)
        if code is not None:
            errors.append({"row": index, "code": code})
            continue
        identity = f"{row['provider']}:{row['external_id']}"
        previous = seen.get(identity)
        if previous is not None and previous != dict(row):
            errors.append({"row": index, "code": "conflicting_duplicate"})
            continue
        seen[identity] = dict(row)
        if previous is None:
            valid.append(dict(row))
    return valid, errors


def _validate_row(row: dict[str, str | None]) -> str | None:
    for key in _HEADER:
        value = row.get(key) or ""
        if value.startswith(_FORMULA_PREFIXES):
            return "formula_like_value"
    if row.get("status") not in {"active", "canceled"}:
        return "unsupported_status"
    for key in ("provider", "external_id", "property_external_id"):
        if not (row.get(key) or "").strip():
            return "missing_field"
    checkin = _parse_offset(row.get("checkin_at") or "")
    checkout = _parse_offset(row.get("checkout_at") or "")
    if checkin is None or checkout is None:
        return "timestamp_requires_offset"
    if checkout <= checkin:
        return "invalid_interval"
    updated = row.get("source_updated_at") or ""
    if updated and _parse_offset(updated) is None:
        return "timestamp_requires_offset"
    return None


def _parse_offset(value: str) -> datetime | None:
    if not value or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed
