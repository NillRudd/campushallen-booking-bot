"""Run on the Pi using the production image as the non-root operator UID.

Creates keys only when absent, never prints values, never reads legacy files.
Mount the private config directory at /provision before running this script.
"""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> None:
    directory = Path("/provision")
    if os.geteuid() == 0 or directory.stat().st_mode & 0o077:
        raise SystemExit("Use the non-root operator UID and a 0700 provisioning directory")
    for name, value in (
        ("secret_key", secrets.token_urlsafe(48)),
        ("encryption_key", Fernet.generate_key().decode()),
    ):
        try:
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w") as stream:
            stream.write(value + "\n")
    print("Production keys ready; values were not printed.")


if __name__ == "__main__":
    main()
