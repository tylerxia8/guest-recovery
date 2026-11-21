from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import UTC, datetime

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Auth headers - Bearer must be case-insensitive
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [redacted]"),
    # Basic auth must be anchored to Authorization header to avoid false positives like "basic check failed"
    (re.compile(r"(?i)authorization:\s*basic\s+[A-Za-z0-9+/]+=*"), "Authorization: Basic [redacted]"),
    # JWT tokens
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[redacted-token]"),
    # API keys
    (re.compile(r"AIza[0-9A-Za-z\-_]{20,}"), "[redacted-key]"),
    # Phone numbers: +1234567890, 123-456-7890, (555) 123-4567
    # NOTE: Removed bare 10-digit pattern to avoid redacting epoch seconds and IDs
    (re.compile(r"\+\d{10,15}"), "[redacted-phone]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[redacted-phone]"),
    (re.compile(r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}"), "[redacted-phone]"),
    # Phone numbers with tel/phone context (bare 10-digit only with context)
    (re.compile(r"(?i)(?:tel|phone)[:\s]*\d{10}\b"), "[redacted-phone]"),
    # URL fragments for onboarding/cleaner links
    (re.compile(r"/onboarding#[^\s\"'<>]+"), "/onboarding#[redacted]"),
    (re.compile(r"/cleaner(?:-portal)?#[^\s\"'<>]+"), "/cleaner#[redacted]"),
    # Bare link tokens: [workspace_id.]grant_id.signature (32.64 or 32.32.64 hex)
    (re.compile(r"(?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}"), "[redacted-link-token]"),
    # URL userinfo (redis://:pw@host, http://user:pass@host, including dotless hosts)
    (re.compile(r"(://)([^/:@\s]*:[^/:@\s]+)@"), r"\1[redacted]@"),
    # Query params: ?code=, &token=, ?access_token=, etc.
    (
        re.compile(r"([?&](?:code|token|access_token|refresh_token|api_key|key|secret|password)=)[^&\s]+"),
        r"\1[redacted]",
    ),
    # Cookie/Set-Cookie headers
    (re.compile(r"(?i)(cookie:\s*)[^\r\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(set-cookie:\s*)[^\r\n]+"), r"\1[redacted]"),
    # mgr_ and portal_ tokens (without Bearer prefix)
    (re.compile(r"\b(mgr_|portal_)[A-Za-z0-9_-]{16,}\b"), "[redacted-token]"),
    # Fernet tokens (gAAAA...)
    (re.compile(r"\bgAAAA[A-Za-z0-9_=-]{40,}\b"), "[redacted-fernet]"),
    # Long hex blobs (40+ hex chars, likely secrets/hashes)
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[redacted-hex]"),
    # Postgres Key (column)=(value) in error details
    (re.compile(r"\bKey\s*\([^)]+\)\s*=\s*\([^)]+\)"), "Key (...)=([redacted])"),
    # Access/entry/door code variations - require "code" suffix to avoid "access: denied"
    # Handles: access_code=, entry code:, door-code=, 'access_code': 'value', "door_code": "value"
    (
        re.compile(
            r"(?i)['\"]?\b(access[\s_-]?code|entry[\s_-]?code|door[\s_-]?code)\b['\"]?\s*[:=]\s*"
            r"(?:'([^']*)'|\"([^\"]*)\"|([^'\",}\s]+))"
        ),
        r"\1=[redacted]",
    ),
    # Email addresses - linear-time pattern (no nested quantifiers)
    (re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,10}\b"), "[redacted-email]"),
    # Password/secret/credential/token/api_key with dict/JSON repr support
    # Handles both quoted and unquoted values, redacts up to closing quote if quoted
    # Also matches compound keys like client_secret, gmail_app_password, access_token
    (
        re.compile(
            r"(?i)['\"]?\b(\w*(?:password|secret|credential|token|api[_-]?key))\b['\"]?\s*[:=]\s*"
            r"(?:'([^']*)'|\"([^\"]*)\"|([^'\",}\s]+))"
        ),
        r"\1=[redacted]",
    ),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class HostedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            fields: dict[str, object] = {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": redact_text(record.getMessage()),
            }
            if record.exc_info and record.exc_info[0]:
                exc_type, exc_value, exc_tb = record.exc_info
                fields["error_type"] = exc_type.__name__
                if exc_value is not None:
                    fields["error_message"] = redact_text(str(exc_value))
                if exc_tb is not None:
                    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
                    fields["traceback"] = redact_text("".join(tb_lines))
            return json.dumps(fields)
        except Exception:
            return json.dumps(
                {
                    "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                    "level": record.levelname.lower(),
                    "logger": record.name,
                    "message": "[formatting error - message redacted]",
                    "format_error": True,
                }
            )


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_text(item) if isinstance(item, str) else item for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_text(item) if isinstance(item, str) else item for key, item in record.args.items()
            }
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger()
    if not any(isinstance(item, RedactingFilter) for item in target.filters):
        target.addFilter(RedactingFilter())
