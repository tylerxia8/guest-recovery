"""Tests for gmail.py SMTP behavior.

SMTP Error Handling (gmail.py:50-57):
- 4xx errors (temporary): Retry up to 3 times with exponential backoff
- 5xx errors (permanent): Fail immediately without retry
- Authentication errors: Fail immediately without retry
- Connection errors (OSError): Retry up to 3 times
"""

from __future__ import annotations

import smtplib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    pass


class TestGmailConnect:
    """Tests for gmail.py connect() SMTP retry behavior."""

    def test_5xx_error_fails_immediately(self) -> None:
        """5xx SMTP errors should fail immediately without retry.

        Mutation: Remove the smtp_code >= 500 check at gmail.py:53.
        Fails: Would retry 3 times before failing.
        """
        from app.services.gmail import connect

        attempt_count = 0

        def mock_smtp_ssl(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            mock = MagicMock()
            mock.login.side_effect = smtplib.SMTPResponseException(550, b"Mailbox unavailable")
            return mock

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", mock_smtp_ssl):
                with pytest.raises(smtplib.SMTPResponseException) as exc_info:
                    connect("/fake/path")

        assert exc_info.value.smtp_code == 550
        assert attempt_count == 1, f"5xx should fail on first attempt, got {attempt_count} attempts"

    def test_4xx_error_retries(self) -> None:
        """4xx SMTP errors should retry up to 3 times.

        Mutation: Change >= 500 to >= 400 at gmail.py:53.
        Fails: Would fail on first 4xx instead of retrying.
        """
        from app.services.gmail import connect

        attempt_count = 0

        def mock_smtp_ssl(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            mock = MagicMock()
            mock.login.side_effect = smtplib.SMTPResponseException(421, b"Try again later")
            mock.close = MagicMock()
            return mock

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", mock_smtp_ssl):
                with patch("time.sleep"):
                    with pytest.raises(smtplib.SMTPResponseException) as exc_info:
                        connect("/fake/path")

        assert exc_info.value.smtp_code == 421
        assert attempt_count == 3, f"4xx should retry 3 times, got {attempt_count} attempts"

    def test_connection_error_retries(self) -> None:
        """Connection errors (OSError) should retry up to 3 times.

        Mutation: Remove OSError from the except clause at gmail.py:50.
        Fails: OSError would propagate immediately.
        """
        from app.services.gmail import connect

        attempt_count = 0

        def mock_smtp_ssl(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            raise OSError("Connection refused")

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", mock_smtp_ssl):
                with patch("time.sleep"):
                    with pytest.raises(OSError):
                        connect("/fake/path")

        assert attempt_count == 3, f"OSError should retry 3 times, got {attempt_count} attempts"

    def test_auth_error_fails_immediately(self) -> None:
        """Authentication errors should fail immediately without retry.

        Mutation: Remove the SMTPAuthenticationError check at gmail.py:46-49.
        Fails: Would retry authentication.
        """
        from app.services.gmail import connect

        attempt_count = 0

        def mock_smtp_ssl(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            mock = MagicMock()
            mock.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
            mock.close = MagicMock()
            return mock

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", mock_smtp_ssl):
                with pytest.raises(smtplib.SMTPAuthenticationError):
                    connect("/fake/path")

        assert attempt_count == 1, f"Auth error should fail on first attempt, got {attempt_count} attempts"

    def test_successful_connection_returns_client(self) -> None:
        """Successful connection should return (client, address) tuple."""
        from app.services.gmail import connect

        mock_client = MagicMock()
        mock_client.login = MagicMock()

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", return_value=mock_client):
                client, address = connect("/fake/path")

        assert client is mock_client
        assert address == "test@example.com"
        mock_client.login.assert_called_once_with("test@example.com", "abcdefghijklmnop")

    def test_4xx_then_success_returns_client(self) -> None:
        """After 4xx retry, successful connection should return client."""
        from app.services.gmail import connect

        attempt_count = 0

        def mock_smtp_ssl(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal attempt_count
            attempt_count += 1
            mock = MagicMock()
            mock.close = MagicMock()
            if attempt_count < 3:
                mock.login.side_effect = smtplib.SMTPResponseException(421, b"Try again")
            else:
                mock.login = MagicMock()
            return mock

        with patch("app.services.gmail.read_credentials", return_value=("test@example.com", "abcdefghijklmnop")):
            with patch("smtplib.SMTP_SSL", mock_smtp_ssl):
                with patch("time.sleep"):
                    client, address = connect("/fake/path")

        assert attempt_count == 3
        assert address == "test@example.com"
