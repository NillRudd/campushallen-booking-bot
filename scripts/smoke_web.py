"""Local browser smoke with temporary users/database and a mocked Campushallen API.

Install Playwright Chromium first, or set CAMPUSHALLEN_BROWSER_CHANNEL=chrome.
No real credentials or booking/cancellation mutations are used.
"""

import os
import socket
import secrets
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import sync_playwright

from campushallen.api import CampushallenHttpClient
from campushallen.services.authentication import create_user
from campushallen.settings import Settings
from campushallen.web.app import create_app


def main() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    artifacts = Path(os.environ.get("CAMPUSHALLEN_SMOKE_ARTIFACTS", "diagnostics/browser-smoke"))
    artifacts.mkdir(parents=True, exist_ok=True)
    local_start = (datetime.now(ZoneInfo("Europe/Stockholm")) + timedelta(days=8)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    activity = {
        "id": 101,
        "name": "Innebandy 85 Vallastaden",
        "groupActivityProduct": {"id": 1, "name": "Innebandy 85"},
        "businessUnit": {"id": 1, "name": "Campushallen"},
        "locations": [{"name": "Vallastaden"}],
        "duration": {
            "start": local_start.isoformat(),
            "end": (local_start + timedelta(minutes=85)).isoformat(),
        },
        "bookableEarliest": (local_start - timedelta(days=7)).isoformat(),
        "slots": {"leftToBook": 5, "total": 15, "totalBookable": 15, "hasWaitingList": True},
    }
    api_calls = []

    def respond(request):
        api_calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={
                    "username": "100",
                    "access_token": "temporary-test-token",
                    "expires_in": 3600,
                },
            )
        assert request.method == "GET", (
            "No booking or cancellation mutations allowed in browser smoke"
        )
        if request.url.path.endswith("/bookings/groupactivities"):
            return httpx.Response(200, json=[])
        if "/apps/" in request.url.path:
            start = datetime.fromisoformat(
                request.url.params["period.start"].replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(request.url.params["period.end"].replace("Z", "+00:00"))
            return httpx.Response(200, json=[activity] if start <= local_start <= end else [])
        return httpx.Response(200, json={"id": 100})

    original_init = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        original_init(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://example.test", transport=httpx.MockTransport(respond)
        )

    with (
        tempfile.TemporaryDirectory(prefix="campushallen-phase2-browser-") as directory,
        patch.object(CampushallenHttpClient, "__init__", init),
    ):
        url = f"sqlite:///{directory}/test.db"
        migration = Config("alembic.ini")
        migration.attributes["database_url"] = url
        command.upgrade(migration, "head")
        app = create_app(
            Settings(
                database_url=url,
                state_dir=Path(directory),
                public_base_url=base_url,
                _env_file=None,
            )
        )
        admin_name, admin_password = "operator-" + secrets.token_hex(4), secrets.token_urlsafe(24)
        friend_name, friend_password = "friend-" + secrets.token_hex(4), secrets.token_urlsafe(24)
        campu_password = secrets.token_urlsafe(24)
        with app.state.sessions() as db:
            create_user(db, admin_name, admin_password, is_admin=True)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel=os.environ.get("CAMPUSHALLEN_BROWSER_CHANNEL") or None, headless=True
                )
                admin = browser.new_context(viewport={"width": 1400, "height": 1000})
                page = admin.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base_url + "/login")
                page.get_by_label("Application username").fill(admin_name)
                page.get_by_label("Password", exact=True).fill(admin_password)
                page.get_by_role("button", name="Log in", exact=True).click()
                page.get_by_role("link", name="Admin", exact=True).click()
                page.get_by_label("Label", exact=True).fill("Browser smoke")
                page.get_by_role("button", name="Generate one-time links").click()
                invite_url = page.get_by_label("Invitation 1 link", exact=True).input_value()
                page.get_by_role("link", name="Return to invitation management").click()
                assert invite_url not in page.content()
                friend = browser.new_context(viewport={"width": 1400, "height": 1000})
                page = friend.new_page()
                page.set_default_timeout(10000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(invite_url)
                page.get_by_label("Choose username", exact=True).fill(friend_name)
                page.get_by_label("Choose website password", exact=True).fill(friend_password)
                page.get_by_label("Confirm website password", exact=True).fill(friend_password)
                page.get_by_role("button", name="Create account", exact=True).click()
                page.get_by_label("Application username").fill(friend_name)
                page.get_by_label("Password", exact=True).fill(friend_password)
                page.get_by_role("button", name="Log in", exact=True).click()
                assert page.url.endswith("/onboarding")
                page.get_by_role("link", name="Accounts", exact=True).click()
                page.get_by_label("Display name").fill("Training account")
                page.get_by_label("Campushallen username").fill("temporary-browser-account")
                page.get_by_role("button", name="Add account", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                assert not page.get_by_label(
                    "Remember Campushallen credentials for automatic reconnection", exact=True
                ).is_checked()
                page.get_by_label("Campushallen password", exact=True).fill(campu_password)
                page.get_by_label(
                    "Remember Campushallen credentials for automatic reconnection", exact=True
                ).check()
                page.get_by_role("button", name="Connect account", exact=True).click()
                assert (
                    "Automatic reconnection:" in page.content()
                    and campu_password not in page.content()
                )
                page.get_by_role("link", name="Rules", exact=True).click()
                page.get_by_role("link", name="+ New rule", exact=True).click()
                page.get_by_label("Rule name", exact=True).fill("Daytime floorball")
                page.get_by_label("Activity", exact=True).fill("Innebandy")
                page.get_by_label("Product name", exact=True).fill("Innebandy 85")
                page.get_by_label("Weekday", exact=True).select_option(str(local_start.weekday()))
                page.get_by_label("Preferred start time", exact=True).fill("12:00")
                page.get_by_label("Facility contains (comma-separated)", exact=True).fill(
                    "Vallastaden"
                )
                page.get_by_role("button", name="Save rule", exact=True).click()
                page.get_by_role("link", name="Accounts", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                page.get_by_role("button", name="Preview matching activities", exact=True).click()
                assert page.get_by_text("DRY_RUN", exact=True).count() == 1
                page.get_by_role("link", name="Overview", exact=True).click()
                assert page.get_by_text("Innebandy 85 Vallastaden", exact=True).count() >= 1
                page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
                mobile = browser.new_context(
                    storage_state=friend.storage_state(), viewport={"width": 390, "height": 844}
                )
                mobile_page = mobile.new_page()
                mobile_page.goto(base_url + "/")
                mobile_page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
                assert mobile_page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.get_by_role("link", name="History", exact=True).click()
                page.get_by_role("button", name="Filter history", exact=True).click()
                assert (
                    page.locator("tbody tr").filter(has_text="Innebandy 85 Vallastaden").count()
                    == 1
                )
                page.get_by_role("link", name="Accounts", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                page.get_by_role("button", name="Forget saved password", exact=True).click()
                assert "Saved password removed" in page.content()
                page.get_by_role("link", name="Security", exact=True).click()
                page.get_by_role("button", name="Log out all sessions", exact=True).click()
                assert page.url.endswith("/login")
                assert errors == [], errors
                browser.close()
            assert all(
                method == "GET" or path.endswith("/auth/login") for method, path in api_calls
            )
            print(
                "Browser smoke passed: admin invitation, registration, onboarding, mocked connection with remember opt-in, basic rule, preview, dashboard, history filters, forget credential, logout-all, desktop/mobile; no real API mutations or JavaScript errors."
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
