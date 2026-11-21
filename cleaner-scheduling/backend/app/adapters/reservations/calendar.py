from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.adapters.errors import CapabilityUnavailable, PermanentFailure

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def assert_feed_url_allowed(url: str, allowlist: set[str]) -> None:
    """Reject feeds that are not HTTPS on an explicit allowlist. Does not fetch."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise PermanentFailure("calendar feed URL must be https with a hostname")
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTS or host not in allowlist:
        raise PermanentFailure("calendar feed host is not allowlisted")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443)}
    except socket.gaierror as exc:
        raise PermanentFailure("calendar feed host did not resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise PermanentFailure("calendar feed host resolves to a blocked address")


def pull_calendar_feed(url: str, allowlist: set[str]) -> None:
    assert_feed_url_allowed(url, allowlist)
    raise CapabilityUnavailable(
        "calendar",
        "Calendar feed fetch is scaffolded and does not perform a live request in M2.",
    )
