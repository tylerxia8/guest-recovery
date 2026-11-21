from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import stat
import time
from email.message import EmailMessage
from pathlib import Path


def read_credentials(filename: str) -> tuple[str, str]:
    descriptor = os.open(Path(filename).expanduser(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Gmail credentials must be an owner-only regular file.")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Gmail credentials are invalid.")
    fields = json.loads(raw)
    if not isinstance(fields, dict) or set(fields) != {"email", "app_password"}:
        raise ValueError("Gmail credentials are invalid.")
    address, password = fields["email"], fields["app_password"]
    if (
        not isinstance(address, str)
        or len(address) > 254
        or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", address)
        or not isinstance(password, str)
        or not re.fullmatch(r"[a-zA-Z0-9]{16}", password)
    ):
        raise ValueError("Gmail credentials are invalid.")
    return address, password


def connect(filename: str) -> tuple[smtplib.SMTP_SSL, str]:
    address, password = read_credentials(filename)
    for attempt in range(3):
        client = None
        try:
            client = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=ssl.create_default_context())
            client.login(address, password)
            return client, address
        except smtplib.SMTPAuthenticationError:
            if client is not None:
                client.close()
            raise
        except (OSError, smtplib.SMTPException) as error:
            if client is not None:
                client.close()
            if isinstance(error, smtplib.SMTPResponseException) and error.smtp_code >= 500:
                raise
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)
    raise RuntimeError("Gmail connection attempts exhausted.")


def send_email(filename: str, message_id: str, recipient: str, subject: str, body: str) -> None:
    client, address = connect(filename)
    try:
        send_connected_email(client, address, message_id, recipient, subject, body)
    finally:
        client.close()


def send_connected_email(
    client: smtplib.SMTP_SSL, address: str, message_id: str, recipient: str, subject: str, body: str
) -> None:
    message = EmailMessage()
    message["From"] = address
    message["To"] = recipient
    message["Subject"] = subject
    # RFC 2822 Message-ID: stable per email send. Each send produces the same
    # Message-ID, but Gmail does not guarantee duplicate suppression on retry.
    message["Message-ID"] = f"<{message_id}@turnover.local>"
    message.set_content(body)
    refused = client.send_message(message)
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)
