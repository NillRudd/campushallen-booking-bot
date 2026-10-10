from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .api import CampushallenHttpClient, save_token_state
from .browser import import_playwright_sync, likely_login_page
from .config import load_config
from .logging_config import configure_logging

LOGGER = logging.getLogger(__name__)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _credentials_from_env(config_path: str | None = None) -> tuple[str, str]:
    config = load_config(config_path)
    values = _load_env_file(config.auth.env_path)
    username = (
        os.environ.get("CAMPUSHALLEN_USERNAME")
        or os.environ.get("CAMPUSHALLEN_EMAIL")
        or values.get("CAMPUSHALLEN_USERNAME")
        or values.get("CAMPUSHALLEN_EMAIL")
    )
    password = os.environ.get("CAMPUSHALLEN_PASSWORD") or values.get("CAMPUSHALLEN_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            f"Missing credentials. Create {config.auth.env_path} with CAMPUSHALLEN_USERNAME "
            "and CAMPUSHALLEN_PASSWORD, chmod 600, then rerun campushallen-login --env."
        )
    return username, password


def api_login(config_path: str | None = None) -> Path:
    config = load_config(config_path)
    configure_logging(config.logging.level, config.logging.file)
    username, password = _credentials_from_env(config_path)
    client = CampushallenHttpClient(config)
    try:
        token_data = client.login(username, password)
        token_path = save_token_state(config, token_data)
        customer_id = token_data.get("username")
        if customer_id:
            client.get_customer(int(customer_id))
    finally:
        client.close()
    return token_path


def interactive_login(config_path: str | None = None) -> Path:
    config = load_config(config_path)
    configure_logging(config.logging.level, config.logging.file)
    sync_playwright, _, _ = import_playwright_sync()
    config.auth.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_args = {
            "headless": False,
            "args": ["--ozone-platform=x11"],
            "env": {"QT_QPA_PLATFORM": "xcb"},
        }
        if Path("/usr/bin/google-chrome").exists():
            launch_args["channel"] = "chrome"
        browser = playwright.chromium.launch(**launch_args)
        context = browser.new_context(
            locale="sv-SE",
            timezone_id=str(config.timezone),
        )
        page = context.new_page()
        page.goto(config.base_url, wait_until="domcontentloaded", timeout=60_000)
        print("Logga in manuellt i webbläsaren som öppnades.")
        print("Tryck Enter här när du ser att du är inloggad på Campushallen.")
        input()
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            LOGGER.debug("Network idle was not reached after login confirmation")
        if likely_login_page(page):
            browser.close()
            raise RuntimeError("Login could not be verified; page still looks like a login page.")
        context.storage_state(path=str(config.auth.storage_state_path))
        os.chmod(config.auth.storage_state_path, 0o600)
        browser.close()
    return config.auth.storage_state_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or refresh saved Campushallen browser session.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--env", action="store_true", help="Login through the BRP API using credentials from .env.")
    args = parser.parse_args()
    if args.env:
        path = api_login(args.config)
        print(f"Saved API auth token state to {path}")
    else:
        path = interactive_login(args.config)
        print(f"Saved storage state to {path}")


if __name__ == "__main__":
    main()
