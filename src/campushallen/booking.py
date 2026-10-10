from __future__ import annotations

import argparse
import json
import logging
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .api import (
    CampushallenHttpClient,
    GroupActivitySlot,
    find_group_activity_slots_for_range,
    find_matching_group_activity_slot,
    find_matching_service_slot,
    select_group_activity_for_release_date,
    select_group_activity_for_date,
)
from .browser import (
    click_booking_control,
    create_context,
    find_candidate_element,
    import_playwright_sync,
    likely_login_page,
    navigate_to_schedule,
    save_diagnostics,
    session_from_match,
)
from .config import AppConfig, load_config
from .logging_config import configure_logging
from .schedule import (
    BookingRule,
    BookingState,
    booking_open_time,
    build_rule_from_args,
    combine_local,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookingResult:
    state: BookingState
    message: str


def wait_until(target: datetime, final_precision_seconds: float) -> None:
    while True:
        now = datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        if remaining > final_precision_seconds:
            time_module.sleep(min(remaining - final_precision_seconds, 60))
        else:
            time_module.sleep(min(remaining, 0.05))


def resolve_target_start_from_now(
    config: AppConfig,
    rule: BookingRule,
    now: datetime,
    date_value: str | None,
) -> datetime:
    if date_value:
        return combine_local(date_value, rule.start_time.strftime("%H:%M"), config.timezone)
    opening_grace = timedelta(hours=12)
    fallback: datetime | None = None
    for day_offset in range(config.schedule.lookahead_days + 1):
        candidate_date = (now + timedelta(days=day_offset)).date()
        if candidate_date.weekday() != rule.weekday:
            continue
        candidate = datetime.combine(candidate_date, rule.start_time, config.timezone)
        if candidate <= now:
            continue
        if fallback is None:
            fallback = candidate
        opens_at = booking_open_time(
            candidate,
            config.booking.opens_days_before,
            same_time=config.booking.opens_at_same_time_as_activity,
        )
        if opens_at >= now - opening_grace:
            return candidate
    if fallback:
        return fallback
    raise ValueError(f"No future {rule.activity} session found within lookahead window")


def resolve_target_start(config: AppConfig, rule: BookingRule, date_value: str | None) -> datetime:
    return resolve_target_start_from_now(config, rule, datetime.now(config.timezone), date_value)


def _candidate_dates(config: AppConfig, rule: BookingRule, now: datetime, date_value: str | None) -> list[date]:
    if date_value:
        target_date = date.fromisoformat(date_value)
        if target_date.weekday() != rule.weekday:
            raise ValueError(f"{date_value} is not the configured weekday for {rule.activity}")
        return [target_date]
    dates: list[date] = []
    for day_offset in range(config.schedule.lookahead_days + 1):
        candidate_date = (now + timedelta(days=day_offset)).date()
        if candidate_date.weekday() == rule.weekday:
            dates.append(candidate_date)
    return dates


def _customer_id_from_token_state(config: AppConfig) -> int:
    if not config.auth.token_state_path.exists():
        raise FileNotFoundError(
            f"Missing API auth token state: {config.auth.token_state_path}. Run scripts/login.py --env first."
        )
    raw = json.loads(config.auth.token_state_path.read_text(encoding="utf-8"))
    customer_id = raw.get("customer_id")
    if customer_id is None:
        raise ValueError(f"Missing customer_id in {config.auth.token_state_path}. Run scripts/login.py --env again.")
    return int(customer_id)


def _booked_group_activity_ids(bookings: list[dict]) -> set[int]:
    ids: set[int] = set()
    for booking in bookings:
        group_activity = booking.get("groupActivity") or {}
        activity_id = group_activity.get("id")
        if activity_id is not None:
            ids.add(int(activity_id))
    return ids


def _verified_booking_for_activity(bookings: list[dict], activity_id: int) -> dict | None:
    for booking in bookings:
        group_activity = booking.get("groupActivity") or {}
        if group_activity.get("id") == activity_id:
            return booking
    return None


def _refresh_slot(
    client: CampushallenHttpClient,
    rule: BookingRule,
    selected: GroupActivitySlot,
    config: AppConfig,
) -> GroupActivitySlot | None:
    period_start = datetime.combine(selected.starts_at.date(), datetime.min.time(), config.timezone)
    period_end = period_start + timedelta(days=1) - timedelta(milliseconds=1)
    slots = find_group_activity_slots_for_range(client, rule, period_start, period_end, config.timezone)
    return next((slot for slot in slots if slot.id == selected.id), None)


def _select_dynamic_group_activity(
    config: AppConfig,
    rule: BookingRule,
    client: CampushallenHttpClient,
    date_value: str | None,
) -> tuple[BookingState, object | None, str]:
    now = datetime.now(config.timezone)
    if date_value:
        selection = select_group_activity_for_date(client, rule, date.fromisoformat(date_value), config.timezone)
    else:
        selection = select_group_activity_for_release_date(
            client,
            rule,
            now.date(),
            now,
            config.schedule.lookahead_days,
            config.timezone,
        )
    if selection.slot:
        return selection.state, selection.slot, selection.message
    if selection.state in {BookingState.AMBIGUOUS_TARGET, BookingState.SUSPICIOUS_TARGET}:
        candidates = "; ".join(slot.raw_text for slot in selection.candidates)
        return selection.state, None, f"{selection.message}. Candidates: {candidates}"
    return selection.state, None, selection.message


def run_booking(
    config: AppConfig,
    rule: BookingRule,
    *,
    date_value: str | None,
    dry_run: bool,
    wait: bool,
    headless: bool,
) -> BookingResult:
    if rule.booking_type == "group_activity" and rule.start_time is None:
        LOGGER.info("Target rule: %s", rule.name or rule.activity)
        client = CampushallenHttpClient(config)
        try:
            state, selected, message = _select_dynamic_group_activity(config, rule, client, date_value)
            if not selected:
                return BookingResult(state, message)
            slot = selected
            if not isinstance(slot, GroupActivitySlot):
                return BookingResult(BookingState.UNKNOWN, "Internal error: selected target is not a group activity")
            session = slot.to_session(rule.activity)
            opens_at = slot.bookable_earliest or booking_open_time(
                slot.starts_at,
                config.booking.opens_days_before,
                same_time=config.booking.opens_at_same_time_as_activity,
            )
            LOGGER.info("Selected group activity slot: %s", session.raw_text)
            LOGGER.info("API booking opens at %s", opens_at.isoformat())

            customer_id = _customer_id_from_token_state(config)
            bookings = client.customer_group_activity_bookings(customer_id)
            if slot.id in _booked_group_activity_ids(bookings):
                LOGGER.info("ALREADY_BOOKED: groupActivity=%s", slot.id)
                return BookingResult(BookingState.BOOKED, f"ALREADY_BOOKED: {session.raw_text}")

            if session.status == BookingState.WAITLIST and not config.booking.allow_waitlist:
                LOGGER.info("SKIPPED_WAITLIST_ONLY: %s", session.raw_text)
                return BookingResult(
                    session.status,
                    f"SKIPPED_WAITLIST_ONLY: {session.raw_text}",
                )
            if session.status == BookingState.FULL:
                return BookingResult(session.status, f"Group activity is full: {session.raw_text}")
            if wait:
                LOGGER.info("Waiting locally until API bookableEarliest")
                wait_until(opens_at, config.booking.final_precision_seconds)
            elif datetime.now(config.timezone) < opens_at:
                LOGGER.info("Booking is not open yet; run with --wait for exact timing")
                if not dry_run:
                    return BookingResult(BookingState.NOT_OPEN, f"Booking is not open yet: {session.raw_text}")
            if dry_run:
                LOGGER.info("Dry-run: final booking request was not sent")
                return BookingResult(BookingState.BOOKABLE, f"Dry-run matched group activity: {session.raw_text}")

            refreshed = _refresh_slot(client, rule, slot, config)
            if not refreshed:
                return BookingResult(BookingState.TARGET_NOT_FOUND, f"Selected activity disappeared before booking: {slot.id}")
            refreshed_session = refreshed.to_session(rule.activity)
            bookings = client.customer_group_activity_bookings(customer_id)
            if refreshed.id in _booked_group_activity_ids(bookings):
                LOGGER.info("ALREADY_BOOKED: groupActivity=%s", refreshed.id)
                return BookingResult(BookingState.BOOKED, f"ALREADY_BOOKED: {refreshed_session.raw_text}")
            if refreshed_session.status == BookingState.WAITLIST and not config.booking.allow_waitlist:
                LOGGER.info("SKIPPED_WAITLIST_ONLY: %s", refreshed_session.raw_text)
                return BookingResult(
                    refreshed_session.status,
                    f"SKIPPED_WAITLIST_ONLY: {refreshed_session.raw_text}",
                )
            if refreshed_session.status == BookingState.FULL:
                return BookingResult(refreshed_session.status, f"Group activity is full: {refreshed_session.raw_text}")

            LOGGER.info("Sending booking request for groupActivity=%s", refreshed.id)
            client.book_group_activity(
                customer_id,
                refreshed.id,
                allow_waiting_list=config.booking.allow_waitlist,
            )
            if config.booking.verify_after_booking:
                verified = _verified_booking_for_activity(
                    client.customer_group_activity_bookings(customer_id),
                    refreshed.id,
                )
                if not verified:
                    return BookingResult(
                        BookingState.UNKNOWN,
                        f"Booking request sent but verification did not find activity {refreshed.id}",
                    )
            LOGGER.info("Booking verified for groupActivity=%s", refreshed.id)
            return BookingResult(BookingState.BOOKED, f"Booking confirmed: {refreshed_session.raw_text}")
        finally:
            client.close()

    starts_at = resolve_target_start(config, rule, date_value)
    opens_at = booking_open_time(
        starts_at,
        config.booking.opens_days_before,
        same_time=config.booking.opens_at_same_time_as_activity,
    )
    LOGGER.info("Target: %s at %s", rule.activity, starts_at.isoformat())
    LOGGER.info("Booking opens at %s", opens_at.isoformat())

    if rule.booking_type == "group_activity":
        client = CampushallenHttpClient(config)
        try:
            slot, same_day_slots = find_matching_group_activity_slot(client, rule, starts_at)
        finally:
            client.close()
        if not slot:
            available = ", ".join(
                f"{slot.starts_at:%H:%M} {slot.name} id={slot.id} left={slot.left_to_book}/{slot.total_bookable}"
                for slot in same_day_slots
            )
            details = available or "no matching group activities returned for that date"
            return BookingResult(
                BookingState.UNKNOWN,
                f"Could not find {rule.activity} group activity {starts_at:%Y-%m-%d %H:%M}. Available: {details}",
            )
        session = slot.to_session(rule.activity)
        if slot.bookable_earliest:
            opens_at = slot.bookable_earliest
            LOGGER.info("API booking opens at %s", opens_at.isoformat())
        LOGGER.info("Found group activity slot: %s", session.raw_text)
        if session.status == BookingState.FULL:
            return BookingResult(session.status, f"Group activity is full: {session.raw_text}")
        if session.status == BookingState.WAITLIST and not config.booking.allow_waitlist:
            return BookingResult(
                session.status,
                f"Only waitlist is available and allow_waitlist is false: {session.raw_text}",
            )
        if wait:
            LOGGER.info("Waiting locally until booking moment")
            wait_until(opens_at, config.booking.final_precision_seconds)
        elif datetime.now(config.timezone) < opens_at:
            LOGGER.info("Booking is not open yet; run with --wait for exact timing")
        if dry_run:
            LOGGER.info("Dry-run: final booking request was not sent")
            return BookingResult(BookingState.BOOKABLE, f"Dry-run matched group activity: {session.raw_text}")
        return BookingResult(
            BookingState.BOOKABLE,
            "READY FOR LIVE TEST: matched group activity but no booking request was sent. "
            f"Target: {session.raw_text}. Perform this real booking now?",
        )

    if rule.booking_type == "service":
        client = CampushallenHttpClient(config)
        try:
            slot, same_day_slots = find_matching_service_slot(client, rule, starts_at)
        finally:
            client.close()
        if not slot:
            available = ", ".join(
                f"{slot.starts_at:%H:%M} {slot.product.name} BU={slot.business_unit_id}"
                for slot in same_day_slots
            )
            details = available or "no slots returned for that date"
            return BookingResult(
                BookingState.UNKNOWN,
                f"Could not find {rule.activity} service slot {starts_at:%Y-%m-%d %H:%M}. Available: {details}",
            )
        session = slot.to_session(rule.activity)
        LOGGER.info("Found service slot: %s", session.raw_text)
        if slot.available_slots <= 0:
            return BookingResult(BookingState.FULL, f"Service slot is full: {session.raw_text}")
        if wait:
            LOGGER.info("Waiting locally until booking moment")
            wait_until(opens_at, config.booking.final_precision_seconds)
        elif datetime.now(config.timezone) < opens_at:
            LOGGER.info("Booking is not open yet; run with --wait for exact timing")
        if dry_run:
            LOGGER.info("Dry-run: final booking request was not sent")
            return BookingResult(BookingState.BOOKABLE, f"Dry-run matched service slot: {session.raw_text}")
        return BookingResult(
            BookingState.BOOKABLE,
            "READY FOR LIVE TEST: matched service slot but no booking request was sent. "
            f"Target: {session.raw_text}. Perform this real booking now?",
        )

    if not config.auth.storage_state_path.exists():
        return BookingResult(
            BookingState.LOGIN_REQUIRED,
            f"Missing saved session: {config.auth.storage_state_path}. Run scripts/login.py first.",
        )

    sync_playwright, TimeoutError, Error = import_playwright_sync()
    with sync_playwright() as playwright:
        browser, context = create_context(playwright, config, headless=headless)
        page = context.new_page()
        try:
            navigate_to_schedule(page, config)
            if likely_login_page(page):
                save_diagnostics(page, config, "login-required")
                return BookingResult(
                    BookingState.LOGIN_REQUIRED,
                    "Saved session is expired or not logged in. Run scripts/login.py again.",
                )

            match = find_candidate_element(page, rule.activity, starts_at)
            if not match:
                save_diagnostics(page, config, "session-not-found")
                return BookingResult(
                    BookingState.UNKNOWN,
                    f"Could not find {rule.activity} {starts_at:%Y-%m-%d %H:%M} on the loaded schedule.",
                )

            session = session_from_match(rule.activity, starts_at, match)
            LOGGER.info("Found %s %s", session.activity, session.starts_at.strftime("%H:%M"))
            if session.status in {BookingState.FULL, BookingState.CANCELLED, BookingState.BOOKED}:
                return BookingResult(session.status, f"Session status is {session.status.value}; no booking sent.")
            if session.status == BookingState.WAITLIST and not config.booking.allow_waitlist:
                return BookingResult(session.status, "Only waitlist appears available and allow_waitlist is false.")
            if wait:
                LOGGER.info("Waiting locally until booking moment")
                wait_until(opens_at, config.booking.final_precision_seconds)
            elif datetime.now(config.timezone) < opens_at:
                LOGGER.info("Booking is not open yet; run with --wait for exact timing")

            if dry_run:
                LOGGER.info("Dry-run: final booking click/request was not sent")
                return BookingResult(BookingState.BOOKABLE, "Dry-run completed; booking was not sent.")

            if not session.element_ref:
                return BookingResult(BookingState.UNKNOWN, "Found session but could not resolve clickable element.")

            LOGGER.info("Booking request sent")
            state = click_booking_control(page, session.element_ref, config)
            if state == BookingState.LOGIN_REQUIRED:
                save_diagnostics(page, config, "login-required-after-click")
                return BookingResult(state, "Session expired during booking.")
            if state == BookingState.BOOKED:
                LOGGER.info("Booking confirmed")
                return BookingResult(state, "Booking confirmed.")
            save_diagnostics(page, config, f"booking-result-{state.value}")
            return BookingResult(state, f"Booking finished with status: {state.value}")
        except (TimeoutError, Error) as exc:
            save_diagnostics(page, config, "playwright-error")
            return BookingResult(BookingState.UNKNOWN, f"Playwright error: {exc}")
        finally:
            browser.close()


def _rules_from_config(config: AppConfig) -> list[BookingRule]:
    return [BookingRule.from_config(raw) for raw in config.bookings]


def _production_report(config: AppConfig, rules: list[BookingRule], run_date: date | None) -> None:
    client = CampushallenHttpClient(config)
    try:
        report_now = datetime.now(config.timezone)
        if run_date:
            report_now = datetime.combine(run_date, datetime.min.time(), config.timezone).replace(hour=18, minute=30)
        customer_id = _customer_id_from_token_state(config)
        booked_ids = _booked_group_activity_ids(client.customer_group_activity_bookings(customer_id))
        print(f"Production run date: {report_now.date().isoformat()}")
        print(f"Production run time: {report_now:%H:%M %Z}")
        for rule in rules:
            print()
            print(f"Rule: {rule.name or rule.activity}")
            period_start = datetime.combine(report_now.date(), datetime.min.time(), config.timezone)
            period_end = period_start + timedelta(days=config.schedule.lookahead_days) - timedelta(milliseconds=1)
            candidates = find_group_activity_slots_for_range(
                client,
                rule,
                period_start,
                period_end,
                config.timezone,
            )
            print("Candidates found:")
            if not candidates:
                print("  none")
            for slot in candidates:
                release = slot.bookable_earliest.astimezone(config.timezone) if slot.bookable_earliest else None
                release_matches = release is not None and release.date() == report_now.date()
                already_booked = slot.id in booked_ids
                print(f"  Activity: {slot.name}")
                print(f"  ID: {slot.id}")
                print(f"  class date: {slot.starts_at.date().isoformat()}")
                print(f"  start: {slot.starts_at:%H:%M}")
                print(f"  bookableEarliest: {release.isoformat() if release else 'unknown'}")
                print(f"  release date matches today: {'yes' if release_matches else 'no'}")
                print(f"  available: {slot.left_to_book}/{slot.total_bookable}")
                print(f"  already booked: {'yes' if already_booked else 'no'}")
                print()
            selection = select_group_activity_for_release_date(
                client,
                rule,
                report_now.date(),
                report_now,
                config.schedule.lookahead_days,
                config.timezone,
            )
            print("SELECTED TARGET:")
            if selection.slot:
                slot = selection.slot
                print(f"  {slot.name}")
                print(f"  ID: {slot.id}")
                print(f"  class date: {slot.starts_at.date().isoformat()}")
                print(f"  start: {slot.starts_at:%H:%M}")
                print(f"  bookableEarliest: {slot.bookable_earliest.isoformat() if slot.bookable_earliest else 'unknown'}")
                print(f"  already booked: {'yes' if slot.id in booked_ids else 'no'}")
            else:
                print(f"  {selection.state.value}: {selection.message}")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Book or dry-run Campushallen activity.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--activity", default=None)
    parser.add_argument("--weekday", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--time", default=None, help="HH:MM")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true", help="Send the booking click/request.")
    parser.add_argument("--wait", action="store_true", help="Wait locally until booking opens.")
    parser.add_argument("--headed", action="store_true", help="Show browser window.")
    parser.add_argument("--production-report", action="store_true", help="Print read-only release-date selection diagnostics.")
    parser.add_argument("--run-date", default=None, help="YYYY-MM-DD production date for --production-report.")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(config.logging.level, config.logging.file)

    if args.activity and args.time:
        rule = build_rule_from_args(args.activity, args.weekday, args.date, args.time)
        rules = [rule]
    else:
        rules = _rules_from_config(config)

    if args.production_report:
        _production_report(config, rules, date.fromisoformat(args.run_date) if args.run_date else None)
        return

    if not args.activity and not args.date:
        today_weekday = datetime.now(config.timezone).weekday()
        rules = [rule for rule in rules if rule.weekday == today_weekday]
        if not rules:
            message = f"No configured booking rule for today's weekday ({today_weekday}); nothing to do."
            LOGGER.info("%s", message)
            print(f"{BookingState.TARGET_NOT_FOUND.value}: {message}")
            return

    dry_run = True if args.dry_run else config.booking.dry_run_default
    if args.live:
        dry_run = False

    exit_code = 0
    for rule in rules:
        result = run_booking(
            config,
            rule,
            date_value=args.date,
            dry_run=dry_run,
            wait=args.wait,
            headless=not args.headed,
        )
        LOGGER.info("%s", result.message)
        print(f"{result.state.value}: {result.message}")
        if result.state not in {BookingState.BOOKED, BookingState.BOOKABLE, BookingState.WAITLIST}:
            exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
