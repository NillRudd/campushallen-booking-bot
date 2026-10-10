from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from campushallen.api import select_group_activity_for_date, select_group_activity_for_release_date
from campushallen.schedule import BookingRule, BookingState


STOCKHOLM = ZoneInfo("Europe/Stockholm")


class FakeClient:
    def __init__(self, activities: list[dict]) -> None:
        self.activities = activities

    def group_activities(self, period_start, period_end, **kwargs) -> list[dict]:
        matching = []
        for activity in self.activities:
            starts_at = datetime.fromisoformat(
                activity["duration"]["start"].replace("Z", "+00:00")
            ).astimezone(STOCKHOLM)
            if period_start <= starts_at <= period_end:
                matching.append(activity)
        return matching


def _rule(**overrides) -> BookingRule:
    values = {
        "activity": "Innebandy",
        "weekday": 1,
        "product_name": "Innebandy 85",
        "web_category_id": 40,
        "facility_contains": ("Vallastaden",),
        "preferred_start_time": time(18, 45),
        "allowed_start_earliest": time(17, 0),
        "allowed_start_latest": time(21, 0),
    }
    values.update(overrides)
    return BookingRule(**values)


def _activity(
    *,
    name: str = "Innebandy 85 Vallastaden",
    activity_id: int = 1,
    start: str = "2026-10-13T16:45:00.000Z",
    end: str = "2026-10-13T18:10:00.000Z",
    bookable_earliest: str = "2026-10-06T16:45:00.000Z",
    left: int = 15,
    total: int = 15,
) -> dict:
    return {
        "id": activity_id,
        "name": name,
        "duration": {"start": start, "end": end},
        "groupActivityProduct": {"id": 1401, "name": name},
        "businessUnit": {"id": 1, "name": "Campushallen/LSIF"},
        "locations": [{"id": 16, "name": "Vallastaden C-salen"}],
        "bookableEarliest": bookable_earliest,
        "slots": {
            "total": total,
            "totalBookable": total,
            "leftToBook": left,
            "hasWaitingList": True,
            "inWaitingList": 0,
        },
    }


def test_tuesday_1845_innebandy_selected() -> None:
    result = select_group_activity_for_date(FakeClient([_activity()]), _rule(), date(2026, 10, 13), STOCKHOLM)
    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.starts_at.strftime("%H:%M") == "18:45"


def test_tuesday_1900_innebandy_selected_when_time_moves() -> None:
    result = select_group_activity_for_date(
        FakeClient([
            _activity(
                start="2026-10-13T17:00:00.000Z",
                end="2026-10-13T18:25:00.000Z",
            )
        ]),
        _rule(),
        date(2026, 10, 13),
        STOCKHOLM,
    )
    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.starts_at.strftime("%H:%M") == "19:00"


def test_other_activity_at_1845_does_not_block_1915_innebandy() -> None:
    result = select_group_activity_for_date(
        FakeClient([
            _activity(name="Yoga 55 Vallastaden", activity_id=10),
            _activity(
                activity_id=11,
                start="2026-10-13T17:15:00.000Z",
                end="2026-10-13T18:40:00.000Z",
            ),
        ]),
        _rule(),
        date(2026, 10, 13),
        STOCKHOLM,
    )
    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.id == 11
    assert result.slot.starts_at.strftime("%H:%M") == "19:15"


def test_two_innebandy_candidates_are_ambiguous_without_distinguishing_config() -> None:
    result = select_group_activity_for_date(
        FakeClient([
            _activity(activity_id=21, start="2026-10-13T16:30:00.000Z", end="2026-10-13T17:55:00.000Z"),
            _activity(activity_id=22, start="2026-10-13T17:30:00.000Z", end="2026-10-13T18:55:00.000Z"),
        ]),
        _rule(preferred_start_time=None),
        date(2026, 10, 13),
        STOCKHOLM,
    )
    assert result.state == BookingState.AMBIGUOUS_TARGET
    assert result.slot is None


def test_no_innebandy_is_target_not_found() -> None:
    result = select_group_activity_for_date(
        FakeClient([_activity(name="Yoga 55 Vallastaden")]),
        _rule(),
        date(2026, 10, 13),
        STOCKHOLM,
    )
    assert result.state == BookingState.TARGET_NOT_FOUND
    assert result.slot is None


def test_morning_innebandy_is_suspicious_outside_allowed_window() -> None:
    result = select_group_activity_for_date(
        FakeClient([
            _activity(
                start="2026-10-13T07:00:00.000Z",
                end="2026-10-13T08:25:00.000Z",
            )
        ]),
        _rule(),
        date(2026, 10, 13),
        STOCKHOLM,
    )
    assert result.state == BookingState.SUSPICIOUS_TARGET
    assert result.slot is None


def test_release_date_selection_ignores_nearer_stale_and_future_releases() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=101,
                start="2026-10-13T16:45:00.000Z",
                end="2026-10-13T18:10:00.000Z",
                bookable_earliest="2026-10-06T16:45:00.000Z",
            ),
            _activity(
                activity_id=102,
                start="2026-10-15T16:45:00.000Z",
                end="2026-10-15T18:10:00.000Z",
                bookable_earliest="2026-10-08T16:45:00.000Z",
            ),
            _activity(
                activity_id=103,
                start="2026-10-20T16:45:00.000Z",
                end="2026-10-20T18:10:00.000Z",
                bookable_earliest="2026-10-13T16:45:00.000Z",
            ),
            _activity(
                activity_id=104,
                start="2026-10-22T16:45:00.000Z",
                end="2026-10-22T18:10:00.000Z",
                bookable_earliest="2026-10-15T16:45:00.000Z",
            ),
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.id == 103


def test_release_date_selection_ignores_existing_nearer_innebandy_booking() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=201,
                start="2026-10-15T16:45:00.000Z",
                end="2026-10-15T18:10:00.000Z",
                bookable_earliest="2026-10-08T16:45:00.000Z",
            ),
            _activity(
                activity_id=202,
                start="2026-10-20T16:45:00.000Z",
                end="2026-10-20T18:10:00.000Z",
                bookable_earliest="2026-10-13T16:45:00.000Z",
            ),
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.id == 202


def test_release_date_selection_nearest_already_booked_does_not_block_new_release() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=301,
                start="2026-10-13T16:45:00.000Z",
                end="2026-10-13T18:10:00.000Z",
                bookable_earliest="2026-10-06T16:45:00.000Z",
                left=0,
            ),
            _activity(
                activity_id=302,
                start="2026-10-20T16:45:00.000Z",
                end="2026-10-20T18:10:00.000Z",
                bookable_earliest="2026-10-13T16:45:00.000Z",
            ),
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.id == 302


def test_release_date_selection_allows_1915_dynamic_start() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=401,
                start="2026-10-20T17:15:00.000Z",
                end="2026-10-20T18:40:00.000Z",
                bookable_earliest="2026-10-13T17:15:00.000Z",
            )
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.BOOKABLE
    assert result.slot is not None
    assert result.slot.id == 401
    assert result.slot.starts_at.strftime("%H:%M") == "19:15"
    assert result.slot.bookable_earliest is not None
    assert result.slot.bookable_earliest.strftime("%H:%M") == "19:15"


def test_release_date_selection_target_not_found_when_no_release_today() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=501,
                start="2026-10-20T16:45:00.000Z",
                end="2026-10-20T18:10:00.000Z",
                bookable_earliest="2026-10-14T16:45:00.000Z",
            )
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.TARGET_NOT_FOUND
    assert result.slot is None


def test_release_date_selection_ambiguous_when_two_targets_release_today() -> None:
    result = select_group_activity_for_release_date(
        FakeClient([
            _activity(
                activity_id=601,
                start="2026-10-20T16:45:00.000Z",
                end="2026-10-20T18:10:00.000Z",
                bookable_earliest="2026-10-13T16:45:00.000Z",
            ),
            _activity(
                activity_id=602,
                start="2026-10-20T17:15:00.000Z",
                end="2026-10-20T18:40:00.000Z",
                bookable_earliest="2026-10-13T17:15:00.000Z",
            ),
        ]),
        _rule(),
        date(2026, 10, 13),
        datetime(2026, 10, 13, 18, 30, tzinfo=STOCKHOLM),
        14,
        STOCKHOLM,
    )

    assert result.state == BookingState.AMBIGUOUS_TARGET
    assert result.slot is None
