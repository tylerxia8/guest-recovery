"""Single-attempt text replies through Hospitable's reservation message endpoint."""

import json
import time
from typing import Literal
from uuid import UUID

import httpx

ReplyStatus = Literal["accepted", "rejected", "unknown"]


def submit_reply(token: str, reservation_id: UUID, body: str) -> ReplyStatus:
    """Return API acceptance only. A transport failure may still have sent."""
    if not token or len(token) > 16384 or not token.isascii() or any(char.isspace() for char in token):
        return "rejected"
    deadline = time.monotonic() + 5
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(4, connect=2)) as client:
            with client.stream(
                "POST",
                f"https://public.api.hospitable.com/v2/reservations/{reservation_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json={"body": body},
            ) as response:
                if response.status_code in (400, 401, 403, 404, 422, 429):
                    return "rejected"
                if response.status_code != 202:
                    return "unknown"
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 16384 or time.monotonic() >= deadline:
                        return "unknown"
                result = json.loads(content)
    except (httpx.HTTPError, ValueError):
        return "unknown"
    data = result.get("data") if isinstance(result, dict) else None
    reference = data.get("sent_reference_id") if isinstance(data, dict) else None
    return "accepted" if isinstance(reference, str) and 0 < len(reference) <= 256 else "unknown"
