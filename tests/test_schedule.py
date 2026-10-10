from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from campushallen.schedule import (
    BookingRule,
    BookingState,
    ClassSession,
    booking_open_time,
    classify_status,
    next_date_for_weekday,
    parse_weekday,
    select_matching_session,
)


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def test_parse_weekday_english_and_swedish() -> None:
    assert parse_weekday("Tuesday") == 1
    assert parse_weekday("tisdag") == 1
    assert parse_weekday("Torsdag") == 3


def test_next_date_for_weekday() -> None:
    now = datetime(2026, 10, 6, 12, 0, tzinfo=STOCKHOLM)
    assert next_date_for_weekday(1, now).isoformat() == "2026-10-06"
    assert next_date_for_weekday(3, now).isoformat() == "2026-10-08"


def test_selects_correct_matching_session() -> None:
    rule = BookingRule("Innebandy", 1, time(18, 45))
    now = datetime(2026, 10, 1, 12, 0, tzinfo=STOCKHOLM)
    sessions = [
        ClassSession("Yoga", datetime(2026, 10, 6, 18, 45, tzinfo=STOCKHOLM)),
        ClassSession("Innebandy", datetime(2026, 10, 6, 18, 45, tzinfo=STOCKHOLM)),
        ClassSession("Innebandy", datetime(2026, 10, 13, 18, 45, tzinfo=STOCKHOLM)),
    ]
    selected = select_matching_session(
        sessions,
        rule,
        now,
        opens_days_before=7,
        lookahead_days=21,
    )
    assert selected == sessions[1]


def test_booking_open_time_same_local_time() -> None:
    starts_at = datetime(2026, 10, 13, 18, 45, tzinfo=STOCKHOLM)
    opens_at = booking_open_time(starts_at, opens_days_before=7)
    assert opens_at.isoformat() == "2026-10-06T18:45:00+02:00"


def test_booking_open_time_handles_dst_boundary() -> None:
    starts_at = datetime(2026, 11, 1, 18, 45, tzinfo=STOCKHOLM)
    opens_at = booking_open_time(starts_at, opens_days_before=7)
    assert opens_at.date().isoformat() == "2026-10-25"
    assert opens_at.timetz().replace(tzinfo=None) == time(18, 45)
    assert opens_at.utcoffset().total_seconds() == 3600


def test_status_classification_booked() -> None:
    assert classify_status("Du är bokad på passet") == BookingState.BOOKED


def test_status_classification_full() -> None:
    assert classify_status("Passet är fullbokad") == BookingState.FULL


def test_status_classification_not_open() -> None:
    assert classify_status("Inte bokningsbar ännu") == BookingState.NOT_OPEN

