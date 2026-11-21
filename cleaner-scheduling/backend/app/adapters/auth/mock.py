from __future__ import annotations

from collections.abc import Callable

import jwt
from jwt import InvalidTokenError

from app.domain.principal import Principal

OperatorRow = tuple[str, tuple[str, ...], bool]


class TokenRejected(Exception):
    def __init__(self, code: str, message: str, status_code: int, subject: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.subject = subject


class MockAuthVerifier:
    """Verify a local HMAC token, then load roles from the operator directory.

    Role claims inside the token are ignored.
    """

    def __init__(self, secret: str, issuer: str, audience: str) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str, lookup: Callable[[str], OperatorRow | None]) -> Principal:
        if not token or len(token) > 4096:
            raise TokenRejected("invalid_token", "Authentication token is invalid.", 401, None)
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            subject = _unverified_subject(token)
            raise TokenRejected("token_expired", "Authentication token is expired.", 401, subject) from None
        except InvalidTokenError:
            raise TokenRejected("invalid_token", "Authentication token is invalid.", 401, None) from None
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise TokenRejected("invalid_token", "Authentication token is invalid.", 401, None)
        row = lookup(subject)
        if row is None:
            raise TokenRejected("unknown_operator", "Operator is not registered.", 403, subject)
        stored_subject, roles, active = row
        if not active:
            raise TokenRejected("inactive_operator", "Operator is deactivated.", 403, stored_subject)
        return Principal(subject=stored_subject, roles=roles)


def _unverified_subject(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["HS256"],
        )
    except InvalidTokenError:
        return None
    subject = payload.get("sub")
    if isinstance(subject, str) and subject:
        return subject
    return None
