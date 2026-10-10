from __future__ import annotations

from zoneinfo import ZoneInfo

from campushallen.api import _parse_group_activity_slot
from campushallen.schedule import BookingRule, BookingState, parse_time


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def test_parse_group_activity_innebandy_slot() -> None:
    slot = _parse_group_activity_slot(
        {
            "id": 4343838,
            "name": "Innebandy 85 Vallastaden",
            "duration": {
                "start": "2026-10-13T16:45:00.000Z",
                "end": "2026-10-13T18:10:00.000Z",
            },
            "groupActivityProduct": {"id": 1401, "name": "Innebandy 85 Vallastaden"},
            "businessUnit": {"id": 1, "name": "Campushallen/LSIF"},
            "bookableEarliest": "2026-10-06T16:45:00.000Z",
            "slots": {
                "total": 15,
                "totalBookable": 15,
                "leftToBook": 0,
                "hasWaitingList": True,
                "inWaitingList": 12,
            },
        },
        STOCKHOLM,
    )

    assert slot is not None
    assert slot.starts_at.isoformat() == "2026-10-13T18:45:00+02:00"
    assert slot.bookable_earliest is not None
    assert slot.bookable_earliest.isoformat() == "2026-10-06T18:45:00+02:00"
    assert slot.total_slots == 15
    assert slot.left_to_book == 0
    assert slot.to_session("Innebandy").status == BookingState.WAITLIST


def test_booking_rule_defaults_to_group_activity() -> None:
    rule = BookingRule("Innebandy", 1, parse_time("18:45"))
    assert rule.booking_type == "group_activity"
    assert rule.web_category_id == 40
