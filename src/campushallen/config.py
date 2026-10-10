from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass(frozen=True)
class AuthConfig:
    storage_state_path: Path = Path("state/storage-state.json")
    token_state_path: Path = Path("state/auth-token.json")
    env_path: Path = Path(".env")
    login_timeout_seconds: int = 300


@dataclass(frozen=True)
class BookingConfig:
    opens_days_before: int = 7
    opens_at_same_time_as_activity: bool = True
    pre_open_start_minutes: int = 10
    final_precision_seconds: float = 2.0
    dry_run_default: bool = True
    allow_waitlist: bool = False
    verify_after_booking: bool = True


@dataclass(frozen=True)
class ScheduleSelectors:
    schedule_root: str | None = None
    class_card: str | None = None
    booking_button: str | None = None


@dataclass(frozen=True)
class ScheduleConfig:
    lookahead_days: int = 21
    activity_url: str | None = None
    selectors: ScheduleSelectors = field(default_factory=ScheduleSelectors)


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: Path = Path("logs/campushallen-bot.log")
    diagnostics_dir: Path = Path("diagnostics")


@dataclass(frozen=True)
class AppConfig:
    timezone: ZoneInfo
    base_url: str
    auth: AuthConfig
    booking: BookingConfig
    schedule: ScheduleConfig
    bookings: list[dict[str, Any]]
    logging: LoggingConfig
    path: Path


def _path(value: str | Path | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


def load_config(config_path: str | Path | None = None) -> AppConfig:
    path = Path(
        config_path
        or os.environ.get("CAMPUSHALLEN_CONFIG")
        or DEFAULT_CONFIG_PATH
    ).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}. Start with: cp config.example.yaml config.yaml"
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    auth_raw = raw.get("auth") or {}
    booking_raw = raw.get("booking") or {}
    schedule_raw = raw.get("schedule") or {}
    selectors_raw = schedule_raw.get("selectors") or {}
    logging_raw = raw.get("logging") or {}

    timezone = ZoneInfo(raw.get("timezone", "Europe/Stockholm"))
    return AppConfig(
        timezone=timezone,
        base_url=raw.get("base_url", "https://campushallen.goactivebooking.com/"),
        auth=AuthConfig(
            storage_state_path=_path(
                auth_raw.get("storage_state_path"), AuthConfig.storage_state_path
            ),
            token_state_path=_path(
                auth_raw.get("token_state_path"), AuthConfig.token_state_path
            ),
            env_path=_path(auth_raw.get("env_path"), AuthConfig.env_path),
            login_timeout_seconds=int(
                auth_raw.get(
                    "login_timeout_seconds", AuthConfig.login_timeout_seconds
                )
            ),
        ),
        booking=BookingConfig(
            opens_days_before=int(
                booking_raw.get("opens_days_before", BookingConfig.opens_days_before)
            ),
            opens_at_same_time_as_activity=bool(
                booking_raw.get(
                    "opens_at_same_time_as_activity",
                    BookingConfig.opens_at_same_time_as_activity,
                )
            ),
            pre_open_start_minutes=int(
                booking_raw.get(
                    "pre_open_start_minutes", BookingConfig.pre_open_start_minutes
                )
            ),
            final_precision_seconds=float(
                booking_raw.get(
                    "final_precision_seconds", BookingConfig.final_precision_seconds
                )
            ),
            dry_run_default=bool(
                booking_raw.get("dry_run_default", BookingConfig.dry_run_default)
            ),
            allow_waitlist=bool(
                booking_raw.get("allow_waitlist", BookingConfig.allow_waitlist)
            ),
            verify_after_booking=bool(
                booking_raw.get(
                    "verify_after_booking", BookingConfig.verify_after_booking
                )
            ),
        ),
        schedule=ScheduleConfig(
            lookahead_days=int(
                schedule_raw.get("lookahead_days", ScheduleConfig.lookahead_days)
            ),
            activity_url=schedule_raw.get("activity_url"),
            selectors=ScheduleSelectors(
                schedule_root=selectors_raw.get("schedule_root"),
                class_card=selectors_raw.get("class_card"),
                booking_button=selectors_raw.get("booking_button"),
            ),
        ),
        bookings=list(raw.get("bookings") or []),
        logging=LoggingConfig(
            level=str(logging_raw.get("level", LoggingConfig.level)),
            file=_path(logging_raw.get("file"), LoggingConfig.file),
            diagnostics_dir=_path(
                logging_raw.get("diagnostics_dir"), LoggingConfig.diagnostics_dir
            ),
        ),
        path=path,
    )
