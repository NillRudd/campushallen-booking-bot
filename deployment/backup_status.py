"""Run the isolated backup helper and publish only safe operational status."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    environment = Path(
        os.environ.get(
            "CAMPUSHALLEN_PROD_ENV", str(Path.home() / ".config/campushallenbot/production.env")
        )
    )
    values = dict(
        line.split("=", 1)
        for line in environment.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    state = Path(values["DATA_DIR"])
    if not state.is_absolute() or not state.is_dir():
        raise ValueError("DATA_DIR must be an existing absolute directory")
    result = subprocess.run(
        [
            str(root / "scripts/production"),
            "run",
            "--rm",
            "--no-deps",
            "--interactive=false",
            "backup",
        ],
        check=False,
    )
    # No database contents, paths, keys or exception text enter the web status.
    temporary = state / ".backup-status.next"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(
            {"ok": result.returncode == 0, "checked_at": datetime.now(timezone.utc).isoformat()},
            file,
        )
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, state / "backup-status.json")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
