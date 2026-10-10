from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from .api import CampushallenHttpClient, find_group_activity_slots
from .browser import (
    create_context,
    find_candidate_element,
    import_playwright_sync,
    likely_login_page,
    navigate_to_schedule,
    save_diagnostics,
)
from .config import load_config
from .logging_config import configure_logging
from .nettrace import NetworkRecorder, timestamped_path
from .schedule import BookingRule, combine_local


def inspect_upcoming_api(config_path: str | None, *, activity: str, days: int) -> None:
    config = load_config(config_path)
    configure_logging(config.logging.level, config.logging.file)
    client = CampushallenHttpClient(config)
    try:
        now = datetime.now(config.timezone)
        print("Upcoming matching classes\n")
        any_match = False
        for day_offset in range(days + 1):
            target_date = (now + timedelta(days=day_offset)).date()
            rule = BookingRule(
                activity=activity,
                weekday=target_date.weekday(),
                product_name=activity,
                web_category_id=40,
                facility_contains=(),
                allowed_start_earliest=None,
                allowed_start_latest=None,
            )
            for slot in find_group_activity_slots(client, rule, target_date, config.timezone):
                any_match = True
                opens = slot.bookable_earliest.strftime("%Y-%m-%d %H:%M") if slot.bookable_earliest else "unknown"
                print(f"{slot.starts_at:%a %Y-%m-%d %H:%M}")
                print(f"Activity: {slot.name}")
                print(f"Location: {slot.facility_text}")
                print(f"Booking opens: {opens}")
                print(f"ID: {slot.id}")
                print(f"Available: {slot.left_to_book} / {slot.total_bookable}")
                print(f"Waiting list: {slot.in_waiting_list if slot.has_waiting_list else 0}")
                print()
        if not any_match:
            print(f"No upcoming matches for {activity!r} in the next {days} day(s).")
    finally:
        client.close()


def _summarize_visible_matches(page: Any, activity: str) -> list[dict[str, str]]:
    return page.evaluate(
        """
        ({ activity }) => {
          const lowerActivity = activity.toLocaleLowerCase('sv-SE');
          const visible = (el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none'
              && rect.width > 0 && rect.height > 0;
          };
          const rows = [];
          for (const node of document.querySelectorAll('body *')) {
            if (!visible(node)) continue;
            const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 900) continue;
            if (!text.toLocaleLowerCase('sv-SE').includes(lowerActivity)) continue;
            const time = text.match(/\\b\\d{1,2}:\\d{2}\\b/);
            rows.push({ text, time: time ? time[0] : '' });
          }
          return rows.slice(0, 40);
        }
        """,
        {"activity": activity},
    )


def inspect_schedule(
    config_path: str | None = None,
    *,
    headless: bool = False,
    activity: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
    trace_api: bool = False,
    interactive: bool = True,
) -> None:
    config = load_config(config_path)
    configure_logging(config.logging.level, config.logging.file)
    sync_playwright, _, _ = import_playwright_sync()
    recorder = NetworkRecorder()

    with sync_playwright() as playwright:
        browser, context = create_context(playwright, config, headless=headless)
        page = context.new_page()
        if trace_api:
            recorder.attach(page)
        navigate_to_schedule(page, config)
        if likely_login_page(page):
            save_diagnostics(page, config, "inspect-login-required")
            browser.close()
            raise SystemExit("Saved session is not authenticated. Run scripts/login.py first.")

        if activity:
            print(f"\nVisible matches for activity: {activity}")
            for idx, row in enumerate(_summarize_visible_matches(page, activity), start=1):
                compact = row["text"]
                if len(compact) > 260:
                    compact = compact[:257] + "..."
                print(f"{idx:02d}. {compact}")

        if activity and date_value and time_value:
            starts_at = combine_local(date_value, time_value, config.timezone)
            match = find_candidate_element(page, activity, starts_at)
            print("\nTarget lookup:")
            if match:
                print(json.dumps(match, indent=2, ensure_ascii=False))
            else:
                print(f"No visible target found for {activity} {date_value} {time_value}")

        save_diagnostics(page, config, "inspect-schedule")
        if interactive and not headless:
            print("\nUse the opened browser to inspect schedule, facility, class details, and my bookings.")
            print("Do not click a final booking button. Press Enter here when finished.")
            input()

        if trace_api:
            output = timestamped_path(config.logging.diagnostics_dir, "sanitized-network.json")
            recorder.write(output)
            print(f"Saved sanitized network trace to {output}")
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Open and inspect Campushallen schedule page.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--activity", default=None)
    parser.add_argument("--date", dest="date_value", default=None)
    parser.add_argument("--time", dest="time_value", default=None)
    parser.add_argument("--days", type=int, default=None, help="Read-only API inspection for upcoming matching classes.")
    parser.add_argument("--trace-api", action="store_true")
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()
    if args.days is not None:
        if not args.activity:
            raise SystemExit("--days requires --activity")
        inspect_upcoming_api(args.config, activity=args.activity, days=args.days)
        return
    if args.date_value:
        date.fromisoformat(args.date_value)
    if args.time_value and not re.fullmatch(r"\d{1,2}:\d{2}", args.time_value):
        raise SystemExit("--time must be HH:MM")
    inspect_schedule(
        args.config,
        headless=args.headless,
        activity=args.activity,
        date_value=args.date_value,
        time_value=args.time_value,
        trace_api=args.trace_api,
        interactive=not args.no_interactive,
    )


if __name__ == "__main__":
    main()
