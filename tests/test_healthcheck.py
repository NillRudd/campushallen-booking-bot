import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from campushallen.healthcheck import healthy_worker


@pytest.mark.parametrize(
    ("age", "dry_run", "stopped", "expected"),
    [(0, 1, None, True), (181, 1, None, False), (0, 0, None, False), (0, 1, "stopped", False)],
)
def test_container_probe_requires_fresh_running_dry_run_worker(
    tmp_path, age, dry_run, stopped, expected
):
    path = tmp_path / "worker.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE worker_states (heartbeat_at TEXT, dry_run INTEGER, stopped_at TEXT)"
        )
        db.execute(
            "INSERT INTO worker_states VALUES (?, ?, ?)",
            ((datetime.now(UTC) - timedelta(seconds=age)).isoformat(), dry_run, stopped),
        )
    assert healthy_worker(f"sqlite:///{path}", 60) is expected


def test_container_probe_never_creates_missing_database(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        healthy_worker(f"sqlite:///{path}", 60)
    assert not path.exists()


def test_live_probe_requires_explicit_expected_mode_and_matching_heartbeat(tmp_path):
    path = tmp_path / "live.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE worker_states (heartbeat_at TEXT, dry_run INTEGER, stopped_at TEXT)"
        )
        db.execute(
            "INSERT INTO worker_states VALUES (?, 0, NULL)", (datetime.now(UTC).isoformat(),)
        )
    assert not healthy_worker(f"sqlite:///{path}", 60)
    assert healthy_worker(f"sqlite:///{path}", 60, dry_run=False)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE worker_states SET dry_run=1")
    assert not healthy_worker(f"sqlite:///{path}", 60, dry_run=False)
