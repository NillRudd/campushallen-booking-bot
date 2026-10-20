import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("mode", ["", "dry-run", "live", "unexpected", "live\nWORKER_MODE=live"])
def test_production_wrapper_selects_live_overlay_only_by_explicit_mode(tmp_path, mode):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    docker.chmod(0o700)
    config = tmp_path / "production.env"
    config.write_text("WORKER_MODE=" + mode + "\n" if mode else "")
    capture = tmp_path / "args"
    result = subprocess.run(
        ["scripts/production", "ps"],
        env={
            **os.environ,
            "PATH": str(fake_bin) + ":" + os.environ["PATH"],
            "CAMPUSHALLEN_PROD_ENV": str(config),
            "CAPTURE": str(capture),
        },
        capture_output=True,
        text=True,
    )
    if mode not in ("", "dry-run", "live"):
        assert result.returncode != 0 and not capture.exists()
        return
    assert result.returncode == 0
    args = capture.read_text().splitlines()
    assert ("deployment/compose.niklas-live.yaml" in args) == (mode == "live")
    assert args[-1] == "ps"


def test_live_overlay_preserves_both_real_booking_flags_and_expected_probe_mode():
    worker = yaml.safe_load(Path("deployment/compose.niklas-live.yaml").read_text())["services"][
        "worker"
    ]
    assert worker["command"] == [
        "python",
        "-m",
        "campushallen.worker",
        "--live",
        "--confirm-real-bookings",
    ]
    assert worker["environment"] == {"WORKER_EXPECTED_MODE": "live"}
