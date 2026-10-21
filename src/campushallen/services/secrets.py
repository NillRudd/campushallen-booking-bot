"""Authenticated token encryption; keys never live in SQLite."""

import json
import os
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from campushallen.settings import Settings


class ReconnectRequired(Exception):
    def __init__(
        self, message: str, *, http_status: int | None = None, endpoint_category: str = "customer"
    ):
        super().__init__(message)
        self.http_status = http_status
        self.endpoint_category = endpoint_category


def runtime_key(settings: Settings, name: str) -> str:
    configured = getattr(settings, name)
    secret_file = getattr(settings, name + "_file", None)
    if not configured and secret_file is not None:
        from pydantic import SecretStr

        try:
            configured = SecretStr(secret_file.read_text(encoding="utf-8").strip())
        except OSError:
            raise ValueError(f"Cannot read {name.upper()} secret file") from None
        if not configured.get_secret_value():
            raise ValueError(f"{name.upper()} secret file is empty")
    if configured:
        value = configured.get_secret_value()
        if name == "secret_key" and len(value) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        return value
    if settings.app_env != "development":
        raise ValueError(f"{name.upper()} is required outside development")
    settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = settings.state_dir / name
    value = (
        Fernet.generate_key().decode() if name == "encryption_key" else secrets.token_urlsafe(48)
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
    return value


def purpose_cipher(master_key: str, purpose: bytes) -> Fernet:
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    Fernet(master_key.encode())
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(
        base64.urlsafe_b64decode(master_key)
    )
    return Fernet(base64.urlsafe_b64encode(derived))


class TokenVault:
    def __init__(self, key: str):
        self.legacy_cipher = Fernet(key.encode())
        self.cipher = purpose_cipher(key, b"campushallen-booker/tokens/v1")
        # Stable, keyed identity comparison without storing a plaintext customer ID.
        import base64
        import hashlib
        import hmac

        self.identity_key = hmac.digest(
            base64.urlsafe_b64decode(key), b"campushallen-booker/identity/v1", hashlib.sha256
        )

    def identity_digest(self, customer_id: int) -> str:
        import hmac

        if isinstance(customer_id, bool) or int(customer_id) <= 0:
            raise ValueError("A verified customer identity is required")
        return hmac.digest(self.identity_key, str(int(customer_id)).encode(), "sha256").hex()

    def seal(self, state: dict[str, Any]) -> str:
        return "v1:" + self.cipher.encrypt(json.dumps(state).encode()).decode()

    def open(self, ciphertext: str | None) -> dict[str, Any]:
        if not ciphertext:
            raise ReconnectRequired("Reconnect this Campushallen account.")
        try:
            cipher = self.cipher if ciphertext.startswith("v1:") else self.legacy_cipher
            payload = ciphertext[3:] if ciphertext.startswith("v1:") else ciphertext
            result = json.loads(cipher.decrypt(payload.encode()))
            if not isinstance(result, dict):
                raise ValueError("Invalid token state")
            return result
        except (InvalidToken, ValueError, TypeError):
            raise ReconnectRequired("Token state is unavailable; reconnect this account.") from None


class CredentialVault:
    """HKDF-SHA256 purpose separation, plus account binding inside the encrypted payload.

    Tokens and credentials use distinct purpose strings. TokenVault still reads
    previous-phase ciphertext with the legacy key for upgrade compatibility.
    """

    def __init__(self, master_key: str):
        self.cipher = purpose_cipher(master_key, b"campushallen-booker/credentials/v1")

    def seal(self, account_id: int, password: str) -> str:
        return self.cipher.encrypt(
            json.dumps({"account_id": account_id, "password": password}).encode()
        ).decode()

    def open(self, account_id: int, ciphertext: str, version: int = 1) -> str:
        try:
            if version != 1:
                raise ValueError("Unsupported version")
            data = json.loads(self.cipher.decrypt(ciphertext.encode()))
            if data["account_id"] != account_id or not isinstance(data["password"], str):
                raise ValueError("Credential belongs to another account")
            return data["password"]
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise ReconnectRequired(
                "Saved credential is unavailable; replace it or reconnect."
            ) from None
