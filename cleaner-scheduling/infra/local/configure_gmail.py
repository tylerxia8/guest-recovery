from __future__ import annotations

import getpass
import json
import os
import re
import tempfile
from pathlib import Path


def main() -> None:
    address = input("Gmail sending address: ").strip()
    if len(address) > 254 or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", address
    ):
        raise SystemExit("Enter a valid Gmail or Google Workspace email address.")
    password = "".join(getpass.getpass("Google app password (hidden): ").split())
    if not re.fullmatch(r"[a-zA-Z0-9]{16}", password):
        raise SystemExit(
            "Use the 16-character app password, not your Google account password."
        )
    root = Path(__file__).resolve().parents[2]
    directory = root / "private-data" / "hospitable"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, filename = tempfile.mkstemp(prefix=".gmail-", dir=directory)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"email": address, "app_password": password}, stream)
        temporary.replace(directory / "gmail.json")
    finally:
        temporary.unlink(missing_ok=True)
    print("Gmail credentials saved privately. No email was sent.")
    print(
        "Restart the local workspace, then use Send test to myself on the Cleaners page."
    )


if __name__ == "__main__":
    main()
