from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_web import post
from test_web import web as web

from campushallen.database.models import User
from campushallen.services.backups import backup_database, verify_restore
from campushallen.services.secrets import runtime_key
from campushallen.settings import Settings
from campushallen.web.app import create_app


def production(app, *, peer="172.30.85.3"):
    settings = app.state.settings.model_copy(
        update={
            "app_env": "production",
            "secure_cookies": True,
            "public_base_url": "https://campushallenbot.se",
            "allowed_hosts": ["campushallenbot.se"],
            "trusted_tunnel_ips": ["172.30.85.3"],
        }
    )
    return TestClient(
        create_app(settings),
        base_url="https://campushallenbot.se",
        client=(peer, 12345),
        headers={
            "CF-Connecting-IP": "192.0.2.7",
            "X-Forwarded-Proto": "https",
            "Origin": "https://campushallenbot.se",
        },
    )


def test_secret_files_are_backwards_compatible_and_never_generated_in_production(tmp_path):
    key = Fernet.generate_key().decode()
    path = tmp_path / "key"
    path.write_text(key + "\n")
    settings = Settings(app_env="production", encryption_key_file=path, _env_file=None)
    assert runtime_key(settings, "encryption_key") == key
    with pytest.raises(ValueError, match="required outside development"):
        runtime_key(settings, "secret_key")
    path.write_text("")
    with pytest.raises(ValueError, match="empty"):
        runtime_key(settings, "encryption_key")
    path.unlink()
    with pytest.raises(ValueError, match="Cannot read"):
        runtime_key(settings, "encryption_key")
    assert not (tmp_path / "secret_key").exists()


def test_production_rejects_insecure_cookie_origin_configuration(web):
    _, app = web
    settings = app.state.settings.model_copy(update={"app_env": "production"})
    with pytest.raises(ValueError, match="requires HTTPS"):
        create_app(settings)


def test_production_host_origin_and_secure_sessions(web):
    _, app = web
    with production(app) as client:
        assert client.get("/login", headers={"host": "attacker.example"}).status_code == 400
        assert client.get("/login").status_code == 200
        bad = client.post("/login", data={}, headers={"Origin": "https://attacker.example"})
        assert bad.status_code == 403 and "ursprung" in bad.text
        response = post(
            client, "/login", {"username": "alice", "password": "correct-horse-battery"}, "/login"
        )
        assert response.status_code == 303
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Referrer-Policy"] == "strict-origin"
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    ("origin", "referer", "expected"),
    [
        ("null", "https://campushallenbot.se/login", 403),
        ("https://attacker.example", "https://campushallenbot.se/login", 403),
        (None, None, 403),
        (None, "https://attacker.example/login", 403),
        (None, "https://campushallenbot.se/login", 303),
    ],
)
def test_production_origin_checks_with_valid_password_and_csrf(web, origin, referer, expected):
    _, app = web
    with production(app) as client:
        client.headers.pop("Origin")
        if origin is not None:
            client.headers["Origin"] = origin
        if referer is not None:
            client.headers["Referer"] = referer
        response = post(
            client, "/login", {"username": "alice", "password": "correct-horse-battery"}, "/login"
        )
        assert response.status_code == expected
        if expected == 403:
            assert "ursprung" in response.text
            assert "session" not in client.cookies
        else:
            assert "Secure" in response.headers["set-cookie"]
            assert client.get("/").status_code == 200


def test_production_password_and_session_forms_keep_origin_and_csrf_checks(web):
    _, app = web
    with production(app) as client:
        assert (
            post(
                client,
                "/login",
                {"username": "alice", "password": "correct-horse-battery"},
                "/login",
            ).status_code
            == 303
        )
        assert client.post("/settings/password", data={}).status_code == 403
        changed = post(
            client,
            "/settings/password",
            {
                "current_password": "correct-horse-battery",
                "new_password": "replacement-website-password",
                "confirmation": "replacement-website-password",
            },
            "/settings",
        )
        assert changed.status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 303
        assert (
            post(
                client,
                "/login",
                {"username": "alice", "password": "replacement-website-password"},
                "/login",
            ).status_code
            == 303
        )
        assert (
            post(client, "/settings/sessions/logout-all", token_path="/settings").status_code == 303
        )
        assert client.get("/", follow_redirects=False).status_code == 303


def test_forwarded_headers_are_ignored_from_untrusted_peers(web):
    _, app = web
    from fastapi import Request

    with production(app, peer="192.0.2.200") as client:

        @client.app.get("/test-peer")
        def probe(request: Request):
            return {"ip": request.client.host, "scheme": request.url.scheme}

        response = client.get(
            "/test-peer",
            headers={
                "X-Forwarded-For": "203.0.113.99",
                "X-Forwarded-Proto": "http",
                "X-Forwarded-Host": "attacker.example",
            },
        )
        assert response.json() == {"ip": "192.0.2.200", "scheme": "https"}


def test_trusted_tunnel_requires_valid_cloudflare_identity_and_https(web):
    _, app = web
    from fastapi import Request

    with production(app) as client:

        @client.app.get("/test-peer")
        def probe(request: Request):
            return {"ip": request.client.host, "scheme": request.url.scheme}

        assert client.get("/test-peer").json() == {"ip": "192.0.2.7", "scheme": "https"}
        assert client.get("/login", headers={"CF-Connecting-IP": "not-an-ip"}).status_code == 400
        assert client.get("/login", headers={"X-Forwarded-Proto": "http"}).status_code == 400


def test_online_backup_retention_and_isolated_restore(web, tmp_path):
    _, app = web
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).is_admin = True
        db.commit()
    target = tmp_path / "backups"
    first = backup_database(app.state.settings.database_url, target, keep=2)
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).username = "changed-after-first-backup"
        db.commit()
    verify_restore(first, runtime_key(app.state.settings, "encryption_key"), expected_user="alice")
    second = backup_database(app.state.settings.database_url, target, keep=2)
    third = backup_database(app.state.settings.database_url, target, keep=2)
    assert not first.exists() and second.exists() and third.exists()
    assert third.stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o700
    assert not list(target.glob("*.partial"))
    with app.state.sessions() as db:
        assert db.get(User, app.state.alice_id).username == "changed-after-first-backup"
    with pytest.raises(ValueError, match="Expected active admin"):
        verify_restore(
            third, runtime_key(app.state.settings, "encryption_key"), expected_user="absent"
        )


def test_backup_does_not_create_missing_database_or_accept_public_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(f"sqlite:///{tmp_path}/missing.db", tmp_path / "backups")
    assert not (tmp_path / "missing.db").exists()


def test_production_compose_never_enables_live_booking_or_publishes_host_ports():
    compose = yaml.safe_load(Path("compose.prod.yaml").read_text())
    services = compose["services"]
    assert set(services) == {"web", "worker", "cloudflared"}
    assert services["web"]["image"] == services["worker"]["image"]
    assert services["worker"]["command"] == ["python", "-m", "campushallen.worker"]
    assert all("ports" not in service for service in services.values())
    assert services["web"]["environment"]["SECURE_COOKIES"] == "true"
    assert services["cloudflared"]["image"].startswith("cloudflare/cloudflared:2026.10.1@sha256:")
    assert "--token-file" in services["cloudflared"]["command"]
    assert "--live" not in str(services["worker"])
    assert "--confirm-real-bookings" not in str(services["worker"])
