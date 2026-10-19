"""SQLite online backups and isolated verification; encryption keys stay separate."""

import os
import secrets
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine import make_url

from campushallen.services.secrets import CredentialVault, TokenVault


def sqlite_path(url: str) -> Path:
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "sqlite"
        or not parsed.database
        or parsed.database == ":memory:"
    ):
        raise ValueError("Backup requires a persistent SQLite database")
    path = Path(parsed.database).resolve(strict=True)
    if not path.is_file():
        raise ValueError("SQLite database file not found")
    return path


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + quote(str(path.resolve(strict=True))) + "?mode=ro", uri=True)


def _consistent_copy(source: Path, destination: Path) -> None:
    with closing(_readonly(source)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer, pages=256, sleep=0.05)
        if writer.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Backup integrity check failed")
        if writer.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Backup foreign-key check failed")


def backup_database(url: str, directory: Path, *, keep: int = 14) -> Path:
    if keep < 1:
        raise ValueError("Keep at least one backup")
    source = sqlite_path(url)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError("Backup directory must be private (mode 0700)")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"campushallen-{stamp}-{secrets.token_hex(3)}.sqlite3"
    temporary = destination.with_suffix(".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        _consistent_copy(source, temporary)
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        temporary.rename(destination)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    # Only our completed snapshots are retained; never delete arbitrary operator files.
    backups = sorted(p for p in directory.glob("campushallen-*.sqlite3") if not p.is_symlink())
    for expired in backups[:-keep]:
        expired.unlink()
    return destination


def verify_restore(backup: Path, encryption_key: str, *, expected_user: str | None = None) -> None:
    """Copies into an automatically removed private directory; never opens production RW."""
    with tempfile.TemporaryDirectory(
        prefix="campushallen-restore-", dir=backup.resolve().parent
    ) as directory:
        restored = Path(directory) / "restored.sqlite3"
        _consistent_copy(backup, restored)
        with closing(_readonly(restored)) as db:
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            if not revision:
                raise ValueError("Restored database is not migrated")
            if (
                expected_user
                and not db.execute(
                    "SELECT 1 FROM users WHERE username=? AND is_admin=1 AND is_active=1",
                    (expected_user.strip().casefold(),),
                ).fetchone()
            ):
                raise ValueError("Expected active admin missing from restore")
        # Exercise the same purpose-separated ciphers without reading real saved credentials.
        tokens = TokenVault(encryption_key)
        payload = {"customer_id": 0, "access_token": "synthetic-restore-check"}
        if tokens.open(tokens.seal(payload)) != payload:
            raise ValueError("Encryption recovery check failed")
        vault = CredentialVault(encryption_key)
        if vault.open(0, vault.seal(0, "synthetic-restore-check")) != "synthetic-restore-check":
            raise ValueError("Credential recovery check failed")
