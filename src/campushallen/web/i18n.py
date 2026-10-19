"""Gettext for website copy; Babel for display dates. Never used by domain services."""

from contextvars import ContextVar
from datetime import datetime, time
from functools import lru_cache
from gettext import GNUTranslations, translation
from pathlib import Path
from zoneinfo import ZoneInfo

from babel.dates import format_datetime, format_time, get_day_names

LOCALE = ContextVar("web_locale", default="sv_SE")
CATALOGS = Path(__file__).parent / "translations"


def gettext_noop(message: str) -> str:
    """Mark deferred messages for extraction without translating stored values."""
    return message


@lru_cache
def translations(locale: str = "sv_SE") -> GNUTranslations:
    return translation("messages", localedir=CATALOGS, languages=[locale])


def gettext(message: str, **values) -> str:
    result = translations(LOCALE.get()).gettext(message)
    return result % values if values else result


def local_datetime(value: datetime | None, timezone: str, locale: str) -> str:
    if value is None:
        return "—"
    return format_datetime(value, "EEEE d MMM y, HH:mm", tzinfo=ZoneInfo(timezone), locale=locale)


def clock(value: time | str | None, locale: str) -> str:
    return (
        format_time(value, "HH:mm", locale=locale) if isinstance(value, time) else str(value or "")
    )


def weekdays(locale: str) -> list[str]:
    names = get_day_names("wide", locale=locale)
    return [names[index].capitalize() for index in range(7)]
