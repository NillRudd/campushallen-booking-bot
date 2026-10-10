from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .schedule import BookingState, ClassSession, classify_status

LOGGER = logging.getLogger(__name__)


def import_playwright_sync():
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc
    return sync_playwright, TimeoutError, Error


def create_context(playwright: Any, config: AppConfig, *, headless: bool = True):
    storage_state = config.auth.storage_state_path
    launch_args: dict[str, Any] = {
        "headless": headless,
        "args": ["--ozone-platform=x11"],
        "env": {"QT_QPA_PLATFORM": "xcb"},
    }
    # The host already has Google Chrome installed. Falling back to bundled
    # Chromium keeps this portable when Playwright browsers are installed.
    if Path("/usr/bin/google-chrome").exists():
        launch_args["channel"] = "chrome"
    browser = playwright.chromium.launch(**launch_args)
    context_args: dict[str, Any] = {
        "locale": "sv-SE",
        "timezone_id": str(config.timezone),
    }
    if storage_state.exists():
        context_args["storage_state"] = str(storage_state)
    context = browser.new_context(**context_args)
    return browser, context


def save_diagnostics(page: Any, config: AppConfig, label: str) -> None:
    diagnostics_dir = config.logging.diagnostics_dir
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(config.timezone).strftime("%Y%m%d-%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", label).strip("-")
    screenshot = diagnostics_dir / f"{stamp}-{safe_label}.png"
    html = diagnostics_dir / f"{stamp}-{safe_label}.html"
    try:
        page.screenshot(path=str(screenshot), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        LOGGER.info("Saved diagnostics: %s and %s", screenshot, html)
    except Exception as exc:  # pragma: no cover - best-effort diagnostics
        LOGGER.warning("Could not save diagnostics: %s", exc)


def likely_login_page(page: Any) -> bool:
    url = page.url.casefold()
    if any(part in url for part in ("login", "logga", "signin")):
        return True
    text = page.locator("body").inner_text(timeout=3000).casefold()
    has_password = "lösenord" in text or "password" in text
    has_login_form = bool(page.locator("input[type='password']").count())
    return has_password or has_login_form


def navigate_to_schedule(page: Any, config: AppConfig) -> None:
    url = config.schedule.activity_url or config.base_url
    LOGGER.info("Opening schedule page: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        LOGGER.debug("Network idle was not reached; continuing with loaded DOM")


def _date_fragments(starts_at: datetime) -> list[str]:
    swedish_months = [
        "januari",
        "februari",
        "mars",
        "april",
        "maj",
        "juni",
        "juli",
        "augusti",
        "september",
        "oktober",
        "november",
        "december",
    ]
    return [
        starts_at.strftime("%Y-%m-%d"),
        starts_at.strftime("%d/%m"),
        starts_at.strftime("%-d/%-m"),
        f"{starts_at.day} {swedish_months[starts_at.month - 1]}",
    ]


def find_candidate_element(page: Any, activity: str, starts_at: datetime) -> dict[str, Any] | None:
    time_fragment = starts_at.strftime("%H:%M")
    date_fragments = _date_fragments(starts_at)
    return page.evaluate(
        """
        ({ activity, timeFragment, dateFragments }) => {
          const visible = (el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style && style.visibility !== 'hidden' && style.display !== 'none'
              && rect.width > 0 && rect.height > 0;
          };
          const cssPath = (el) => {
            if (el.id) return `#${CSS.escape(el.id)}`;
            const parts = [];
            while (el && el.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
              let part = el.nodeName.toLowerCase();
              if (el.classList && el.classList.length) {
                part += '.' + Array.from(el.classList).slice(0, 3).map(CSS.escape).join('.');
              }
              const parent = el.parentElement;
              if (parent) {
                const same = Array.from(parent.children).filter(child => child.nodeName === el.nodeName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
              }
              parts.unshift(part);
              el = parent;
            }
            return parts.join(' > ');
          };
          const lowerActivity = activity.toLocaleLowerCase('sv-SE');
          const nodes = Array.from(document.querySelectorAll('body *')).filter(visible);
          const matches = [];
          for (const node of nodes) {
            const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!text) continue;
            const lower = text.toLocaleLowerCase('sv-SE');
            const hasActivity = lower.includes(lowerActivity);
            const hasTime = text.includes(timeFragment);
            const hasDate = dateFragments.some(fragment => lower.includes(fragment.toLocaleLowerCase('sv-SE')));
            if (hasActivity && hasTime && (hasDate || text.length < 800)) {
              const button = Array.from(node.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]'))
                .find(visible);
              matches.push({
                selector: cssPath(node),
                text,
                buttonText: button ? (button.innerText || button.value || '').trim() : '',
                area: node.getBoundingClientRect().width * node.getBoundingClientRect().height,
              });
            }
          }
          matches.sort((a, b) => a.area - b.area || a.text.length - b.text.length);
          return matches[0] || null;
        }
        """,
        {
            "activity": activity,
            "timeFragment": time_fragment,
            "dateFragments": date_fragments,
        },
    )


def click_booking_control(page: Any, card_selector: str, config: AppConfig) -> BookingState:
    selector = config.schedule.selectors.booking_button
    card = page.locator(card_selector).first
    if selector:
        button = card.locator(selector).first
    else:
        button = card.get_by_role("button", name=re.compile("boka|book|reserv", re.I)).first
        if button.count() == 0:
            button = card.locator("a, button, [role='button'], input[type='button'], input[type='submit']").first
    button_text = button.inner_text(timeout=5000) if button.count() else card.inner_text(timeout=5000)
    state = classify_status(button_text)
    if state in {BookingState.FULL, BookingState.CANCELLED, BookingState.NOT_OPEN, BookingState.BOOKED}:
        return state
    button.click(timeout=10_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    return classify_status(page.locator("body").inner_text(timeout=10_000))


def session_from_match(activity: str, starts_at: datetime, match: dict[str, Any]) -> ClassSession:
    text = match.get("text") or ""
    status = classify_status(f"{match.get('buttonText') or ''} {text}")
    return ClassSession(
        activity=activity,
        starts_at=starts_at,
        status=status,
        element_ref=match.get("selector"),
        raw_text=text,
    )
