from __future__ import annotations

import json
import logging

from app.logging_redaction import HostedFormatter, RedactingFilter, redact_text
from app.services.redaction import redact_for_storage, redact_for_viewer


def test_log_redaction_removes_secrets() -> None:
    secret_phone = "+17045550123"
    token = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    message = f"Bearer {token} phone {secret_phone} access code=4455 AIzaSyA123456789012345678901234"
    redacted = redact_text(message)
    assert secret_phone not in redacted
    assert token not in redacted
    assert "4455" not in redacted
    assert "AIzaSyA123456789012345678901234" not in redacted
    logger = logging.getLogger("test.redaction")
    logger.addFilter(RedactingFilter())
    logger.handlers.clear()
    stream = logging.StreamHandler()
    captured: list[str] = []

    class ListStream:
        def write(self, value: str) -> None:
            captured.append(value)

        def flush(self) -> None:
            return None

    stream.stream = ListStream()  # type: ignore[assignment]
    logger.addHandler(stream)
    logger.setLevel(logging.INFO)
    logger.info("contact %s", secret_phone)
    rendered = "".join(captured)
    assert secret_phone not in rendered


def test_viewer_serializer_drops_money_and_contact_fields() -> None:
    payload = {
        "schedule": {"amount_cents": 16500, "property": "synthetic"},
        "phone_e164": "+17045550100",
        "nested": [{"rate_snapshot": {"flat_rate_cents": 100}, "status": "published"}],
    }
    viewed = redact_for_viewer(payload)
    assert viewed == {"schedule": {"property": "synthetic"}, "nested": [{"status": "published"}]}
    stored = redact_for_storage(payload)
    assert isinstance(stored, dict)
    assert stored["schedule"]["amount_cents"] == 16500
    assert "phone_e164" not in stored


def test_hosted_formatter_includes_exception_details() -> None:
    """HostedFormatter must emit error_type, error_message, and traceback for exc_info=True."""
    logger = logging.getLogger("test.formatter")
    logger.handlers.clear()
    logger.setLevel(logging.ERROR)

    captured: list[str] = []

    class ListStream:
        def write(self, value: str) -> None:
            captured.append(value)

        def flush(self) -> None:
            return None

    handler = logging.StreamHandler()
    handler.stream = ListStream()  # type: ignore[assignment]
    handler.setFormatter(HostedFormatter())
    logger.addHandler(handler)

    try:
        raise ValueError("Database connection failed: timeout after 30s")
    except ValueError:
        logger.error("workspace_email_failed workspace_id=abc123", exc_info=True)

    output = "".join(captured).strip()
    parsed = json.loads(output)

    assert parsed["level"] == "error"
    assert parsed["logger"] == "test.formatter"
    assert "workspace_email_failed" in parsed["message"]
    assert parsed["error_type"] == "ValueError"
    assert "Database connection failed" in parsed["error_message"]
    assert "traceback" in parsed
    assert "ValueError" in parsed["traceback"]
    assert "raise ValueError" in parsed["traceback"]


def test_hosted_formatter_redacts_secrets_in_exception() -> None:
    """HostedFormatter must redact secrets, tokens, and emails in exception messages."""
    logger = logging.getLogger("test.formatter.secrets")
    logger.handlers.clear()
    logger.setLevel(logging.ERROR)

    captured: list[str] = []

    class ListStream:
        def write(self, value: str) -> None:
            captured.append(value)

        def flush(self) -> None:
            return None

    handler = logging.StreamHandler()
    handler.stream = ListStream()  # type: ignore[assignment]
    handler.setFormatter(HostedFormatter())
    logger.addHandler(handler)

    secret_email = "user@example.com"
    secret_token = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    secret_phone = "+17045550199"

    try:
        raise RuntimeError(f"Auth failed for {secret_email} with token {secret_token} phone {secret_phone}")
    except RuntimeError:
        logger.error("auth_error", exc_info=True)

    output = "".join(captured).strip()
    parsed = json.loads(output)

    assert secret_email not in parsed["error_message"]
    assert secret_token not in parsed["error_message"]
    assert secret_phone not in parsed["error_message"]
    assert "[redacted-email]" in parsed["error_message"]
    assert "[redacted-token]" in parsed["error_message"]
    assert "[redacted-phone]" in parsed["error_message"]

    assert secret_email not in parsed["traceback"]
    assert secret_token not in parsed["traceback"]
    assert secret_phone not in parsed["traceback"]


def test_hosted_formatter_handles_no_exception() -> None:
    """HostedFormatter works correctly when exc_info is not set."""
    logger = logging.getLogger("test.formatter.noexc")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    captured: list[str] = []

    class ListStream:
        def write(self, value: str) -> None:
            captured.append(value)

        def flush(self) -> None:
            return None

    handler = logging.StreamHandler()
    handler.stream = ListStream()  # type: ignore[assignment]
    handler.setFormatter(HostedFormatter())
    logger.addHandler(handler)

    logger.info("normal_message key=value")

    output = "".join(captured).strip()
    parsed = json.loads(output)

    assert parsed["level"] == "info"
    assert "normal_message" in parsed["message"]
    assert "error_type" not in parsed
    assert "error_message" not in parsed
    assert "traceback" not in parsed


def test_redact_text_handles_email_addresses() -> None:
    """redact_text must redact email addresses."""
    text = "Failed to send to user@example.com and admin@test.org"
    redacted = redact_text(text)
    assert "user@example.com" not in redacted
    assert "admin@test.org" not in redacted
    assert "[redacted-email]" in redacted


def test_redact_text_handles_password_patterns() -> None:
    """redact_text must redact password and secret patterns."""
    text = "password=mysecret123 and api_key=abc123def"
    redacted = redact_text(text)
    assert "mysecret123" not in redacted
    assert "abc123def" not in redacted
    assert "password=[redacted]" in redacted
    assert "api_key=[redacted]" in redacted


def test_redact_text_handles_onboarding_link_tokens() -> None:
    """redact_text must redact onboarding and cleaner-portal link tokens in URLs."""
    onboarding_url = "https://app.example.com/onboarding#abc123def456.fedcba9876543210.0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    cleaner_url = "https://app.example.com/cleaner#workspace123.abc123def456.fedcba9876543210.0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    cleaner_portal_url = "https://app.example.com/cleaner-portal#token.signature"

    redacted_onboarding = redact_text(onboarding_url)
    redacted_cleaner = redact_text(cleaner_url)
    redacted_portal = redact_text(cleaner_portal_url)

    assert "/onboarding#[redacted]" in redacted_onboarding
    assert "abc123def456" not in redacted_onboarding
    assert "/cleaner#[redacted]" in redacted_cleaner
    assert "workspace123" not in redacted_cleaner
    assert "/cleaner#[redacted]" in redacted_portal


def test_redact_text_handles_bare_link_tokens() -> None:
    """redact_text must redact bare cleaner/onboarding link token shapes."""
    grant_id = "12345678901234567890123456789012"
    signature = "0123456789abcdef" * 4
    bare_token = f"{grant_id}.{signature}"
    workspace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with_workspace = f"{workspace_id}.{grant_id}.{signature}"

    redacted_bare = redact_text(f"Link {bare_token} found")
    redacted_ws = redact_text(f"Link {with_workspace} found")

    assert grant_id not in redacted_bare
    assert "[redacted-link-token]" in redacted_bare
    assert workspace_id not in redacted_ws
    assert "[redacted-link-token]" in redacted_ws


def test_redact_text_handles_dict_repr_secrets() -> None:
    """redact_text must redact secrets in dict/JSON repr like {'access_code': '4455'}."""
    dict_repr = "{'access_code': '4455', 'other': 'value'}"
    json_repr = '{"password": "secret123", "user": "admin"}'
    mixed = "config={'secret': 'xyz', 'api_key': 'abc123'}"

    redacted_dict = redact_text(dict_repr)
    redacted_json = redact_text(json_repr)
    redacted_mixed = redact_text(mixed)

    assert "4455" not in redacted_dict
    assert "access_code=[redacted]" in redacted_dict
    assert "secret123" not in redacted_json
    assert "password=[redacted]" in redacted_json
    assert "xyz" not in redacted_mixed
    assert "abc123" not in redacted_mixed


def test_redact_text_handles_secrets_in_traceback() -> None:
    """redact_text must redact secrets appearing in tracebacks."""
    traceback_text = """Traceback (most recent call last):
  File "app.py", line 42, in send_email
    result = api.call(tok="eyJhbGciOiJIUzI1NiJ9.payload.signature")
  File "api.py", line 10, in call
    raise ValueError("Auth failed with {'password': 'secret123'}")
ValueError: Auth failed with {'password': 'secret123'}"""

    redacted = redact_text(traceback_text)

    assert "eyJhbGciOiJIUzI1NiJ9.payload.signature" not in redacted
    assert "secret123" not in redacted
    assert "[redacted-token]" in redacted
    assert "password=[redacted]" in redacted


def test_redact_text_handles_chained_exceptions() -> None:
    """redact_text must redact secrets in chained exception messages."""
    chained = """Traceback (most recent call last):
  File "auth.py", line 5, in login
    connect(credential="abc123secret")
ConnectionError: Failed to connect

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "app.py", line 10, in main
    raise AuthError(f"Login failed for user@example.com") from e
AuthError: Login failed for user@example.com"""

    redacted = redact_text(chained)

    assert "abc123secret" not in redacted
    assert "user@example.com" not in redacted
    assert "credential=[redacted]" in redacted
    assert "[redacted-email]" in redacted


def test_redact_text_handles_access_code_variations() -> None:
    """redact_text must handle various access_code formats."""
    plain = "access code=1234"
    underscore = "access_code: 5678"
    quoted_key = "'access_code': '9012'"
    double_quoted = '"entry_code": "3456"'
    door_code = "door code=7890"

    assert "1234" not in redact_text(plain)
    assert "5678" not in redact_text(underscore)
    assert "9012" not in redact_text(quoted_key)
    assert "3456" not in redact_text(double_quoted)
    assert "7890" not in redact_text(door_code)


def test_redact_text_handles_basic_auth() -> None:
    """redact_text must redact Authorization: Basic headers."""
    auth_header = "Authorization: Basic dXNlcm5hbWU6cGFzc3dvcmQ="
    redacted = redact_text(auth_header)
    assert "dXNlcm5hbWU6cGFzc3dvcmQ=" not in redacted
    assert "Authorization: Basic [redacted]" in redacted


def test_redact_text_handles_parenthetical_phone() -> None:
    """redact_text must redact phone numbers like (555) 123-4567."""
    phone = "Call (555) 123-4567 for support"
    redacted = redact_text(phone)
    assert "(555) 123-4567" not in redacted
    assert "[redacted-phone]" in redacted


def test_redact_text_handles_phone_with_context() -> None:
    """redact_text must redact 10-digit phone numbers with tel/phone context."""
    phone_with_tel = "Contact tel:5551234567 for support"
    phone_with_phone = "Call phone: 5551234567 immediately"

    redacted_tel = redact_text(phone_with_tel)
    redacted_phone = redact_text(phone_with_phone)

    assert "5551234567" not in redacted_tel
    assert "[redacted-phone]" in redacted_tel
    assert "5551234567" not in redacted_phone
    assert "[redacted-phone]" in redacted_phone


def test_redact_text_handles_url_userinfo() -> None:
    """redact_text must redact URL userinfo including dotless hosts."""
    redis_url = "redis://:secretpassword@redis:6379/0"
    http_url = "http://user:pass123@api.example.com/data"
    dotless = "redis://:pw@redis/db"

    redacted_redis = redact_text(redis_url)
    redacted_http = redact_text(http_url)
    redacted_dotless = redact_text(dotless)

    assert "secretpassword" not in redacted_redis
    assert "://[redacted]@" in redacted_redis
    assert "pass123" not in redacted_http
    assert "://[redacted]@" in redacted_http
    assert "pw" not in redacted_dotless or "://[redacted]@" in redacted_dotless


def test_redact_text_handles_query_params() -> None:
    """redact_text must redact ?code=, token=, and similar query params."""
    oauth_callback = "https://example.com/callback?code=abc123def456&state=xyz"
    api_url = "https://api.example.com/data?access_token=secret789&other=value"
    key_param = "https://api.example.com?api_key=myapikey123"

    redacted_oauth = redact_text(oauth_callback)
    redacted_api = redact_text(api_url)
    redacted_key = redact_text(key_param)

    assert "abc123def456" not in redacted_oauth
    assert "code=[redacted]" in redacted_oauth
    assert "secret789" not in redacted_api
    assert "access_token=[redacted]" in redacted_api
    assert "myapikey123" not in redacted_key
    assert "api_key=[redacted]" in redacted_key


def test_redact_text_handles_cookie_headers() -> None:
    """redact_text must redact Cookie/Set-Cookie header values."""
    cookie = "Cookie: session_id=abc123; auth_token=xyz789"
    set_cookie = "Set-Cookie: auth=secret123; Path=/; HttpOnly"

    redacted_cookie = redact_text(cookie)
    redacted_set = redact_text(set_cookie)

    assert "abc123" not in redacted_cookie
    assert "xyz789" not in redacted_cookie
    assert "Cookie: [redacted]" in redacted_cookie
    assert "secret123" not in redacted_set
    assert "Set-Cookie: [redacted]" in redacted_set


def test_redact_text_handles_mgr_portal_tokens() -> None:
    """redact_text must redact mgr_ and portal_ tokens without Bearer prefix."""
    mgr_token = "Token mgr_abcdef1234567890abcdef"
    portal_token = "Using portal_xyz123456789012345678"

    redacted_mgr = redact_text(mgr_token)
    redacted_portal = redact_text(portal_token)

    assert "mgr_abcdef1234567890abcdef" not in redacted_mgr
    assert "[redacted-token]" in redacted_mgr
    assert "portal_xyz123456789012345678" not in redacted_portal
    assert "[redacted-token]" in redacted_portal


def test_redact_text_handles_fernet_tokens() -> None:
    """redact_text must redact Fernet tokens (gAAAA...)."""
    fernet = "encrypted: gAAAABbcdef1234567890abcdefghijklmnopqrstuvwxyz123456"

    redacted = redact_text(fernet)

    assert "gAAAABbcdef1234567890abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "[redacted-fernet]" in redacted


def test_redact_text_handles_long_hex_blobs() -> None:
    """redact_text must redact long hex blobs (40+ chars)."""
    sha256_hash = "a" * 64
    sha1_hash = "b" * 40
    text_with_hash = f"Hash: {sha256_hash} and {sha1_hash}"

    redacted = redact_text(text_with_hash)

    assert sha256_hash not in redacted
    assert sha1_hash not in redacted
    assert "[redacted-hex]" in redacted


def test_redact_text_handles_postgres_key_details() -> None:
    """redact_text must redact Postgres Key (column)=(value) in error details."""
    pg_error = 'Key (email)=(user@secret.com) already exists in table "users"'

    redacted = redact_text(pg_error)

    assert "user@secret.com" not in redacted
    assert "Key (...)=([redacted])" in redacted


def test_redact_text_does_not_redact_uuids() -> None:
    """UUIDs must NOT be redacted."""
    uuid_text = "workspace_id=550e8400-e29b-41d4-a716-446655440000 assigned"
    redacted = redact_text(uuid_text)

    assert "550e8400-e29b-41d4-a716-446655440000" in redacted
    assert "workspace_id" in redacted


def test_redact_text_does_not_redact_workspace_id() -> None:
    """workspace_id values must NOT be redacted."""
    text = "Processing workspace_id=abc123def456 with status=active"
    redacted = redact_text(text)

    assert "workspace_id=abc123def456" in redacted


def test_redact_text_does_not_redact_iso_timestamps() -> None:
    """ISO timestamps must NOT be redacted."""
    iso_time = "Event at 2026-11-10T14:30:00+00:00 completed"
    redacted = redact_text(iso_time)

    assert "2026-11-10T14:30:00+00:00" in redacted


def test_redact_text_does_not_redact_epoch_timestamps() -> None:
    """Epoch timestamps (10-digit numbers) must NOT be redacted."""
    epoch = "Timestamp: 1731249000 recorded"
    redacted = redact_text(epoch)

    assert "1731249000" in redacted


def test_redact_text_does_not_redact_basic_check_failed() -> None:
    """'basic check failed' must NOT be redacted (not a Basic auth header)."""
    text = "Validation: basic check failed for input"
    redacted = redact_text(text)

    assert "basic check failed" in redacted


def test_redact_text_does_not_redact_access_denied() -> None:
    """'access: denied' must NOT be redacted (not an access code)."""
    text = "Permission error: access: denied for user"
    redacted = redact_text(text)

    assert "access: denied" in redacted


def test_redact_text_handles_quoted_values() -> None:
    """When values are quoted, redact up to the closing quote."""
    single_quoted = "{'password': 'my secret value'}"
    double_quoted = '{"access_code": "1234 5678"}'

    redacted_single = redact_text(single_quoted)
    redacted_double = redact_text(double_quoted)

    assert "my secret value" not in redacted_single
    assert "password=[redacted]" in redacted_single
    assert "1234 5678" not in redacted_double
    assert "access_code=[redacted]" in redacted_double


def test_redact_text_performance_50k_chars() -> None:
    """redact_text must complete in reasonable time for 50k chars (no quadratic patterns)."""
    import time

    large_text = "a" * 50000
    email_heavy = ("user@example.com " * 1000)[:50000]
    mixed = "password=secret " * 2500 + "normal text" * 1000

    start = time.monotonic()
    redact_text(large_text)
    plain_time = time.monotonic() - start

    start = time.monotonic()
    redact_text(email_heavy)
    email_time = time.monotonic() - start

    start = time.monotonic()
    redact_text(mixed)
    mixed_time = time.monotonic() - start

    assert plain_time < 1.0, f"Plain text took {plain_time:.2f}s (should be < 1s)"
    assert email_time < 1.0, f"Email-heavy text took {email_time:.2f}s (should be < 1s)"
    assert mixed_time < 1.0, f"Mixed text took {mixed_time:.2f}s (should be < 1s)"


def test_redact_text_handles_compound_secret_keys() -> None:
    """redact_text must redact compound keys like client_secret, gmail_app_password, access_token.

    Mutation: Remove the \\w* prefix from the password/secret/token regex at logging_redaction.py:63.
    Result: Would fail to redact client_secret, gmail_app_password, access_token, etc.
    """
    client_secret = "config: client_secret=abc123supersecret"
    gmail_app_pw = "GMAIL_APP_PASSWORD=wxyz-abcd-efgh-ijkl"
    access_tok = "header: access_token: bearer_token_value_here"
    refresh_tok = "OAuth refresh_token='my_refresh_xyz'"
    app_secret = "app_secret=mysecretvalue"

    redacted_client = redact_text(client_secret)
    redacted_gmail = redact_text(gmail_app_pw)
    redacted_access = redact_text(access_tok)
    redacted_refresh = redact_text(refresh_tok)
    redacted_app = redact_text(app_secret)

    assert "abc123supersecret" not in redacted_client, "client_secret value not redacted"
    assert "client_secret=[redacted]" in redacted_client
    assert "wxyz-abcd-efgh-ijkl" not in redacted_gmail, "gmail_app_password value not redacted"
    assert "GMAIL_APP_PASSWORD=[redacted]" in redacted_gmail
    assert "bearer_token_value_here" not in redacted_access, "access_token value not redacted"
    assert "access_token=[redacted]" in redacted_access
    assert "my_refresh_xyz" not in redacted_refresh, "refresh_token value not redacted"
    assert "refresh_token=[redacted]" in redacted_refresh
    assert "mysecretvalue" not in redacted_app, "app_secret value not redacted"
    assert "app_secret=[redacted]" in redacted_app


def test_redact_text_does_not_over_redact_non_secrets() -> None:
    """Non-secret compound keys must NOT be redacted.

    Mutation: Make the password/secret/token regex too greedy.
    Result: Would redact status_code, error_code, workspace_id, etc.
    """
    status = "Response: status_code=200"
    error = "Failed with error_code=INVALID_INPUT"
    workspace = "workspace_id=abc123def456"
    reservation = "reservation_id=res_12345"
    cleaner = "cleaner_id=cln_abcdef"

    redacted_status = redact_text(status)
    redacted_error = redact_text(error)
    redacted_workspace = redact_text(workspace)
    redacted_reservation = redact_text(reservation)
    redacted_cleaner = redact_text(cleaner)

    assert "status_code=200" in redacted_status, "status_code was incorrectly redacted"
    assert "error_code=INVALID_INPUT" in redacted_error, "error_code was incorrectly redacted"
    assert "workspace_id=abc123def456" in redacted_workspace, "workspace_id was incorrectly redacted"
    assert "reservation_id=res_12345" in redacted_reservation, "reservation_id was incorrectly redacted"
    assert "cleaner_id=cln_abcdef" in redacted_cleaner, "cleaner_id was incorrectly redacted"
