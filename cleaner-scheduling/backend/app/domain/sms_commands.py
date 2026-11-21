from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"V(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class SmsCommand:
    kind: str
    service_date: date | None = None
    version: int | None = None
    task_token: str | None = None
    reason: str | None = None


def parse_sms_command(raw: str) -> SmsCommand:
    """Parse one command. The text is data, never executed."""
    collapsed = " ".join(raw.strip().split())
    if not collapsed:
        return SmsCommand("reject", reason="empty")
    parts = collapsed.split(" ")
    verb = parts[0].upper()
    if verb == "STOP" and len(parts) == 1:
        return SmsCommand("stop")
    if verb == "HELP" and len(parts) == 1:
        return SmsCommand("help")
    if verb == "CONFIRM":
        if len(parts) == 1:
            return SmsCommand("confirm")
        if len(parts) == 3 and _DATE.fullmatch(parts[1]) and _VERSION.fullmatch(parts[2]):
            return SmsCommand(
                "confirm",
                service_date=date.fromisoformat(parts[1]),
                version=int(parts[2][1:]),
            )
        return SmsCommand("reject", reason="malformed_confirm")
    if verb == "DONE":
        if len(parts) != 2 or not parts[1]:
            return SmsCommand("reject", reason="malformed_done")
        return SmsCommand("done", task_token=parts[1])
    if verb == "UNAVAILABLE":
        if len(parts) == 1:
            return SmsCommand("unavailable")
        if len(parts) == 2 and _DATE.fullmatch(parts[1]):
            return SmsCommand("unavailable", service_date=date.fromisoformat(parts[1]))
        return SmsCommand("reject", reason="malformed_unavailable")
    return SmsCommand("reject", reason="unknown_command")
