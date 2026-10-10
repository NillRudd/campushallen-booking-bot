from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from campushallen.booking import resolve_target_start_from_now
from campushallen.schedule import BookingRule


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def _config():
    return SimpleNamespace(
        timezone=STOCKHOLM,
        schedule=SimpleNamespace(lookahead_days=21),
        booking=SimpleNamespace(
            opens_days_before=7,
            opens_at_same_time_as_activity=True,
        ),
    )


def test_automatic_target_is_session_whose_booking_opens_now() -> None:
    rule = BookingRule("Innebandy", 1, time(18, 45))
    now = datetime(2026, 10, 6, 18, 35, tzinfo=STOCKHOLM)
    target = resolve_target_start_from_now(_config(), rule, now, None)
    assert target.isoformat() == "2026-10-13T18:45:00+02:00"


def test_automatic_target_keeps_recently_opened_next_week_session() -> None:
    rule = BookingRule("Innebandy", 1, time(18, 45))
    now = datetime(2026, 10, 6, 21, 0, tzinfo=STOCKHOLM)
    target = resolve_target_start_from_now(_config(), rule, now, None)
    assert target.isoformat() == "2026-10-13T18:45:00+02:00"

