from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from app.services.email_addresses import normalize_email


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare one private pilot invitation without enabling signup.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        email = normalize_email(args.email)
    except ValueError:
        parser.error("Enter a valid invitation email.")
    if email is None:
        parser.error("Enter a valid invitation email.")
    try:
        origin = urlsplit(args.base_url)
        port = origin.port
    except ValueError:
        parser.error("Use a valid HTTPS origin.")
    if (
        origin.scheme != "https"
        or port == 0
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
    ):
        parser.error("Use the app's HTTPS origin without a path, query or fragment.")
    token = secrets.token_urlsafe(32)
    payload = {
        "invitation_url": f"{args.base_url.rstrip('/')}/signup#invite={token}",
        "backend_variables": {
            "WORKSPACE_INVITE_SIGNUP_ENABLED": "true",
            "WORKSPACE_INVITE_EMAIL": email.lower(),
            "WORKSPACE_INVITE_TOKEN_HASH": hashlib.sha256(token.encode()).hexdigest(),
            "WORKSPACE_INVITE_EXPIRES_AT": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "WORKSPACE_REGISTRATION_ENABLED": "false",
        },
        "frontend_variables": {
            "WORKSPACE_INVITE_SIGNUP_ENABLED": "true",
            "WORKSPACE_REGISTRATION_ENABLED": "false",
        },
    }
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
    print(f"Invitation saved privately to {args.output.resolve()}. No service settings were changed.")


if __name__ == "__main__":
    main()
