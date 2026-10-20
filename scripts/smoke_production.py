"""Isolated production-image/Compose smoke, with no tunnel or Campushallen accounts.

Build campushallenbot:production-test first. Temporary keys/data are removed on exit.
This never starts cloudflared or touches the Pi. No real booking is possible.
"""

import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> None:
    if os.getuid() == 0:
        raise SystemExit("Run this local smoke as a non-root development user")
    with tempfile.TemporaryDirectory(prefix="campushallen-prod-smoke-") as temporary:
        root = Path(temporary)
        config, data, backups = (root / name for name in ("config", "data", "backups"))
        for path in (config, data, backups):
            path.mkdir(mode=0o700)
        for name, value in (
            ("secret_key", secrets.token_urlsafe(48)),
            ("encryption_key", Fernet.generate_key().decode()),
            ("cloudflare_tunnel_token", "unused-local-smoke-token"),
        ):
            path = config / name
            path.touch(mode=0o600)
            path.write_text(value)
        image = os.environ.get("CAMPUSHALLEN_PROD_TEST_IMAGE", "campushallenbot:production-test")
        env = root / "production.env"
        env.write_text(
            f"APP_IMAGE={image}\nAPP_UID={os.getuid()}\nAPP_GID={os.getgid()}\n"
            f"CONFIG_DIR={config}\nDATA_DIR={data}\nBACKUP_DIR={backups}\n"
        )
        compose = [
            "docker",
            "compose",
            "-p",
            "campushallen-prod-smoke",
            "--env-file",
            str(env),
            "-f",
            "compose.prod.yaml",
        ]

        def run(*args):
            subprocess.run([*compose, *args], check=True)

        try:
            run("config", "--quiet")  # Configuration contains paths only, never secret values.
            run("run", "--rm", "--no-deps", "web", "alembic", "upgrade", "head")
            run("run", "--rm", "--no-deps", "web", "alembic", "check")
            run("up", "-d", "--wait", "--wait-timeout", "150", "web", "worker")
            run(
                "exec",
                "-T",
                "web",
                "python",
                "-c",
                """
from urllib.request import Request, urlopen
from importlib.util import find_spec
import os
assert os.getuid() != 0
assert find_spec("playwright") is None and find_spec("pytest") is None
request = Request("http://127.0.0.1:8000/login", headers={"Host":"campushallenbot.se"})
response = urlopen(request)
assert 'lang="sv"' in response.read().decode()
assert response.headers["Cache-Control"] == "private, no-store"
print("Production image: non-root, Swedish login, private/no-store; no browser/test packages.")
""",
            )
            run(
                "exec",
                "-T",
                "web",
                "python",
                "-c",
                """
import secrets
from campushallen.settings import Settings
from campushallen.database.session import make_engine, session_factory
from campushallen.services.authentication import create_user
settings = Settings()
engine = make_engine(settings.database_url)
with session_factory(engine)() as db:
    create_user(db, "restore-smoke", secrets.token_urlsafe(32), is_admin=True)
engine.dispose()
""",
            )
            run("stop", "worker")
            run("run", "--rm", "--no-deps", "--interactive=false", "backup")
            snapshot = next(backups.glob("campushallen-*.sqlite3"))
            run("start", "worker")
            run(
                "exec",
                "-T",
                "worker",
                "python",
                "-m",
                "campushallen.cli",
                "restore-test",
                "/backups/" + snapshot.name,
                "--expect-user",
                "restore-smoke",
            )
            run("restart", "web", "worker")
            run("up", "-d", "--wait", "--wait-timeout", "150", "web", "worker")
            run(
                "exec",
                "-T",
                "web",
                "python",
                "-c",
                """
from sqlalchemy import select
from campushallen.database.models import User
from campushallen.database.session import make_engine, session_factory
from campushallen.settings import Settings
engine = make_engine(Settings().database_url)
with session_factory(engine)() as db:
    assert db.scalar(select(User).where(User.username == "restore-smoke")) is not None
engine.dispose()
print("Database and keys persisted across container restart.")
""",
            )
            run("ps")
            print(
                "Isolated production smoke passed. No tunnel or Campushallen account was configured."
            )
        finally:
            subprocess.run([*compose, "down"], check=False)


if __name__ == "__main__":
    main()
