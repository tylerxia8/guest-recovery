from __future__ import annotations

import re

_EMAIL = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)


def normalize_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if len(value) > 254 or len(value.split("@", 1)[0]) > 64 or _EMAIL.fullmatch(value) is None:
        raise ValueError("Enter a valid email address.")
    local, domain = value.rsplit("@", 1)
    return f"{local}@{domain.lower()}"
