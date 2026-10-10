from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Iterable
from zoneinfo import ZoneInfo


WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "måndag": 0,
    "tisdag": 1,
    "tuesday": 1,
    "tue": 1,
    "onsdag": 2,
    "wednesday": 2,
    "wed": 2,
    "torsdag": 3,
    "thursday": 3,
    "thu": 3,
    "fredag": 4,
    "friday": 4,
    "fri": 4,
    "lördag": 5,
    "lordag": 5,
    "saturday": 5,
    "sat": 5,
    "söndag": 6,
    "sondag": 6,
    "sunday": 6,
    "sun": 6,
}


class BookingState(str, Enum):
    BOOKABLE = "bookable"
    BOOKED = "booked"
    FULL = "full"
    WAITLIST = "waitlist"
    NOT_OPEN = "not_open"
    CANCELLED = "cancelled"
    LOGIN_REQUIRED = "login_required"
    TARGET_NOT_FOUND = "target_not_found"
    AMBIGUOUS_TARGET = "ambiguous_target"
    SUSPICIOUS_TARGET = "suspicious_target"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BookingRule:
    activity: str
    weekday: int
    start_time: time | None = None
    booking_type: str = "group_activity"
    web_category_id: int = 40
    name: str | None = None
    product_name: str | None = None
    product_ids: tuple[int, ...] = ()
    business_unit_ids: tuple[int, ...] = ()
    product_name_contains: tuple[str, ...] = ()
    facility_contains: tuple[str, ...] = ()
    preferred_start_time: time | None = None
    allowed_start_earliest: time | None = time(17, 0)
    allowed_start_latest: time | None = time(21, 0)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "BookingRule":
        weekday = parse_weekday(raw["weekday"])
        start_time = parse_time(raw["start_time"]) if raw.get("start_time") else None
        preferred_start_time = (
            parse_time(raw["preferred_start_time"]) if raw.get("preferred_start_time") else start_time
        )
        allowed_window = raw.get("allowed_start_window") or {}
        return cls(
            activity=raw.get("activity") or raw.get("product_name") or raw.get("name", "Activity"),
            weekday=weekday,
            start_time=start_time,
            booking_type=str(raw.get("booking_type", "group_activity")),
            web_category_id=int(raw.get("web_category_id", 40)),
            name=raw.get("name"),
            product_name=raw.get("product_name"),
            product_ids=_int_tuple(raw.get("product_ids")),
            business_unit_ids=_int_tuple(raw.get("business_unit_ids")),
            product_name_contains=_str_tuple(
                raw.get("product_name_contains"),
                default=(),
            ),
            facility_contains=_str_tuple(raw.get("facility_contains"), default=()),
            preferred_start_time=preferred_start_time,
            allowed_start_earliest=(
                parse_time(allowed_window["earliest"])
                if allowed_window.get("earliest")
                else cls.allowed_start_earliest
            ),
            allowed_start_latest=(
                parse_time(allowed_window["latest"])
                if allowed_window.get("latest")
                else cls.allowed_start_latest
            ),
        )


@dataclass(frozen=True)
class ClassSession:
    activity: str
    starts_at: datetime
    status: BookingState = BookingState.UNKNOWN
    title: str | None = None
    location: str | None = None
    element_ref: str | None = None
    raw_text: str = ""

    def matches(self, rule: BookingRule, tolerance_minutes: int = 0) -> bool:
        if rule.activity.casefold() not in self.activity.casefold():
            return False
        if self.starts_at.weekday() != rule.weekday:
            return False
        if rule.start_time is None:
            return True
        target = datetime.combine(self.starts_at.date(), rule.start_time, self.starts_at.tzinfo)
        delta = abs((self.starts_at - target).total_seconds())
        return delta <= tolerance_minutes * 60


def parse_weekday(value: str) -> int:
    key = value.strip().casefold()
    if key not in WEEKDAYS:
        raise ValueError(f"Unknown weekday: {value}")
    return WEEKDAYS[key]


def parse_time(value: str) -> time:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value)
    if not match:
        raise ValueError(f"Invalid time: {value}")
    hour, minute = int(match.group(1)), int(match.group(2))
    return time(hour=hour, minute=minute)


def _int_tuple(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, int)):
        return (int(value),)
    return tuple(int(item) for item in value)


def _str_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def next_date_for_weekday(
    weekday: int, now: datetime, include_today: bool = True
) -> date:
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0 and not include_today:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).date()


def booking_open_time(
    starts_at: datetime, opens_days_before: int, same_time: bool = True
) -> datetime:
    if same_time:
        return starts_at - timedelta(days=opens_days_before)
    return datetime.combine(
        starts_at.date() - timedelta(days=opens_days_before),
        time.min,
        starts_at.tzinfo,
    )


def should_start_runner(
    opens_at: datetime, now: datetime, pre_open_start_minutes: int
) -> bool:
    return now >= opens_at - timedelta(minutes=pre_open_start_minutes)


def select_matching_session(
    sessions: Iterable[ClassSession],
    rule: BookingRule,
    now: datetime,
    opens_days_before: int,
    lookahead_days: int,
    tolerance_minutes: int = 0,
) -> ClassSession | None:
    horizon = now + timedelta(days=lookahead_days)
    candidates = [
        session
        for session in sessions
        if now <= session.starts_at <= horizon
        and session.matches(rule, tolerance_minutes=tolerance_minutes)
        and booking_open_time(session.starts_at, opens_days_before) <= horizon
    ]
    return sorted(candidates, key=lambda session: session.starts_at)[0] if candidates else None


def classify_status(text: str) -> BookingState:
    normalized = " ".join(text.casefold().split())
    if any(needle in normalized for needle in ("logga in", "login", "session expired")):
        return BookingState.LOGIN_REQUIRED
    if any(needle in normalized for needle in ("redan bokad", "du är bokad", "booked")):
        return BookingState.BOOKED
    if any(needle in normalized for needle in ("fullbokad", "fullt", "full")):
        return BookingState.FULL
    if any(needle in normalized for needle in ("reserv", "väntelista", "waitlist")):
        return BookingState.WAITLIST
    if any(needle in normalized for needle in ("ej bokningsbar", "inte bokningsbar", "not open")):
        return BookingState.NOT_OPEN
    if any(needle in normalized for needle in ("inställd", "cancelled", "canceled")):
        return BookingState.CANCELLED
    if any(needle in normalized for needle in ("boka", "book")):
        return BookingState.BOOKABLE
    return BookingState.UNKNOWN


def build_rule_from_args(activity: str, weekday: str | None, date_value: str | None, start_time: str | None) -> BookingRule:
    if weekday:
        parsed_weekday = parse_weekday(weekday)
    elif date_value:
        parsed_weekday = date.fromisoformat(date_value).weekday()
    else:
        raise ValueError("Either --weekday or --date is required")
    return BookingRule(
        activity=activity,
        weekday=parsed_weekday,
        start_time=parse_time(start_time) if start_time else None,
        product_name=activity,
        preferred_start_time=parse_time(start_time) if start_time else None,
    )


def combine_local(date_value: str, time_value: str, timezone: ZoneInfo) -> datetime:
    return datetime.combine(date.fromisoformat(date_value), parse_time(time_value), timezone)
