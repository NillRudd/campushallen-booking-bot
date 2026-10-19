"""User-run hidden prompt on the Pi; never pass a tunnel token through chat or argv."""

import getpass
import os
from pathlib import Path


def main() -> None:
    directory = Path.home() / ".config/campushallenbot"
    if directory.stat().st_mode & 0o077:
        raise SystemExit("Configuration directory must have mode 0700")
    path = directory / "cloudflare_tunnel_token"
    if path.exists():
        raise SystemExit("Token file already exists; use a deliberate rotation procedure")
    token = getpass.getpass("Cloudflare tunnel token (hidden): ").strip()
    if len(token) < 64 or any(character.isspace() for character in token):
        raise SystemExit("Invalid token format; nothing saved")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(token + "\n")
    print("Tunnel token saved privately; value was not displayed.")


if __name__ == "__main__":
    main()
