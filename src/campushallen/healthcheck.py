"""Small, read-only container probe; avoid loading the application on each check."""

import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path


def healthy_worker(database_url: str, poll_seconds: float) -> bool:
    if not database_url.startswith("sqlite:///") or poll_seconds <= 0:
        return False
    path = Path(database_url.removeprefix("sqlite:///"))
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        row = db.execute(
            "SELECT heartbeat_at, dry_run FROM worker_states "
            "WHERE stopped_at IS NULL ORDER BY heartbeat_at DESC LIMIT 1"
        ).fetchone()
    if row is None or row[1] != 1:
        return False
    heartbeat = datetime.fromisoformat(row[0]).replace(tzinfo=UTC)
    return heartbeat > datetime.now(UTC) - timedelta(seconds=max(90, poll_seconds * 3))


def main() -> None:
    if sys.argv[1:] != ["worker"]:
        raise SystemExit(2)
    try:
        healthy = healthy_worker(
            os.environ["DATABASE_URL"], float(os.environ.get("WORKER_POLL_SECONDS", "60"))
        )
    except (KeyError, ValueError, OSError, sqlite3.Error):
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
