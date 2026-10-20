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
                admin = browser.new_context(
                    locale="sv-SE",
                    permissions=["clipboard-read", "clipboard-write"],
                    viewport={"width": 1400, "height": 1000},
                )
                page = admin.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base_url + "/login")
                assert page.locator("html").get_attribute("lang") == "sv"
                page.screenshot(path=str(artifacts / "login.png"), full_page=True)
                page.get_by_label("Användarnamn").fill(admin_name)
                page.get_by_label("Lösenord", exact=True).fill(admin_password)
                with page.expect_request(
                    lambda request: request.method == "POST" and request.url == base_url + "/login"
                ) as login_request:
                    page.get_by_role("button", name="Logga in", exact=True).click()
                # A browser computes Origin from Referrer-Policy. Raw HTTP clients miss this.
                assert login_request.value.all_headers().get("origin") == base_url
                page.get_by_role("link", name="Administration", exact=True).click()
                page.screenshot(path=str(artifacts / "admin.png"), full_page=True)
                page.get_by_label("Namn/etikett", exact=True).fill("Browser smoke")
                page.get_by_role("button", name="Skapa inbjudan").click()
                invite_url = page.get_by_label("Länk till inbjudan 1", exact=True).input_value()
                page.get_by_role("button", name="Kopiera länk", exact=True).click()
                page.get_by_role("status").filter(has_text="Länken har kopierats").wait_for()
                assert page.evaluate("navigator.clipboard.readText()") == invite_url
                page.get_by_role("link", name="Tillbaka till inbjudningarna").click()
                assert invite_url not in page.content()
                friend = browser.new_context(
                    locale="sv-SE", viewport={"width": 1400, "height": 1000}
                )
                page = friend.new_page()
                page.set_default_timeout(10000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(invite_url)
                page.screenshot(path=str(artifacts / "invite.png"), full_page=True)
                page.get_by_label("Välj användarnamn", exact=True).fill(friend_name)
                page.get_by_label("Välj lösenord för webbplatsen", exact=True).fill(friend_password)
                page.get_by_label("Bekräfta lösenord för webbplatsen", exact=True).fill(
                    friend_password
                )
                page.get_by_role("button", name="Skapa konto", exact=True).click()
                page.get_by_label("Användarnamn").fill(friend_name)
                page.get_by_label("Lösenord", exact=True).fill(friend_password)
                page.get_by_role("button", name="Logga in", exact=True).click()
                assert page.url.endswith("/onboarding")
                page.screenshot(path=str(artifacts / "onboarding.png"), full_page=True)
                page.get_by_role("link", name="Campushallen-konton", exact=True).click()
                page.get_by_label("Kontonamn").fill("Training account")
                page.get_by_label("Användarnamn hos Campushallen").fill("temporary-browser-account")
                page.get_by_role("button", name="Lägg till konto", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                assert not page.get_by_label(
                    "Spara inloggningsuppgifter för automatisk återanslutning", exact=True
                ).is_checked()
                page.get_by_label("Lösenord för Campushallen", exact=True).fill(campu_password)
                page.get_by_label(
                    "Spara inloggningsuppgifter för automatisk återanslutning", exact=True
                ).check()
                page.get_by_role("button", name="Anslut konto", exact=True).click()
                page.screenshot(path=str(artifacts / "account.png"), full_page=True)
                assert (
                    "Automatisk återanslutning:" in page.content()
                    and campu_password not in page.content()
                )
                page.get_by_role("link", name="Bokningsregler", exact=True).click()
                page.get_by_role("link", name="+ Ny bokningsregel", exact=True).click()
                page.screenshot(path=str(artifacts / "rule.png"), full_page=True)
                page.get_by_label("Regelns namn", exact=True).fill("Daytime floorball")
                page.get_by_label("Pass", exact=True).fill("Innebandy")
                page.get_by_label("Passets namn hos Campushallen", exact=True).fill("Innebandy 85")
                page.get_by_label("Veckodag", exact=True).select_option(str(local_start.weekday()))
                page.get_by_label("Önskad starttid", exact=True).fill("12:00")
                page.get_by_label("Anläggning (separera flera med kommatecken)", exact=True).fill(
                    "Vallastaden"
                )
                page.get_by_role("button", name="Spara regel", exact=True).click()
                page.get_by_role("link", name="Campushallen-konton", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                page.get_by_role(
                    "button", name="Förhandsgranska matchande pass", exact=True
                ).click()
                assert (
                    page.get_by_text("Testkörning – ingen bokning gjordes", exact=True).count() == 1
                )
                page.get_by_role("link", name="Översikt", exact=True).click()
                assert page.get_by_text("Innebandy 85 Vallastaden", exact=True).count() >= 1
                page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
                mobile = browser.new_context(
                    locale="sv-SE",
                    storage_state=friend.storage_state(),
                    viewport={"width": 390, "height": 844},
                )
                mobile_page = mobile.new_page()
                mobile_page.goto(base_url + "/")
                mobile_page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
                assert mobile_page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.get_by_role("link", name="Historik", exact=True).click()
                page.get_by_role("button", name="Filtrera historiken", exact=True).click()
                page.screenshot(path=str(artifacts / "history.png"), full_page=True)
                assert (
                    page.locator("tbody tr").filter(has_text="Innebandy 85 Vallastaden").count()
                    == 1
                )
                page.get_by_role("link", name="Campushallen-konton", exact=True).click()
                page.get_by_role("link", name="Training account", exact=True).click()
                page.get_by_role("button", name="Ta bort sparat lösenord", exact=True).click()
                assert "Det sparade lösenordet har tagits bort" in page.content()
                page.get_by_role("link", name="Säkerhet", exact=True).click()
                page.screenshot(path=str(artifacts / "security.png"), full_page=True)
                page.get_by_role("button", name="Logga ut alla sessioner", exact=True).click()
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
