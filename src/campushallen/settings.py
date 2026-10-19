"""Web/worker settings, separate from the legacy bot's YAML configuration."""

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///state/web.db"
    secret_key: SecretStr | None = None
    encryption_key: SecretStr | None = None
    campushallen_timezone: str = "Europe/Stockholm"
    secure_cookies: bool = False
    session_hours: int = 12
    state_dir: Path = Path("state")
    worker_poll_seconds: float = 60
    worker_concurrency: int = 8

    @field_validator("campushallen_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("session_hours", "worker_concurrency")
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Must be positive")
        return value

    @field_validator("worker_poll_seconds")
    @classmethod
    def positive_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Must be positive")
        return value
