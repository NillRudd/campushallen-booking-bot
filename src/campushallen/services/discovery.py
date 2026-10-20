"""Read-only schedule browsing, separate from strict rule selection in BookingEngine.

Cache only allowlisted occurrence metadata. No tokens, raw responses or credentials.
The cache is bounded and short lived; execution never uses this browsing cache.
"""

import hashlib
import json
import threading
import time as clock
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from campushallen.api import (
    GroupActivitySlot,
    _group_activity_matches_rule,
    _parse_group_activity_slot,
    _slot_in_allowed_window,
)
from campushallen.database.models import CampushallenAccount
from campushallen.services.rules import RuleInput, domain_rule
from campushallen.services.secrets import ReconnectRequired


class DiscoveryUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ActivityOccurrence:
    activity_id: int
    product_id: int | None
    product_name: str
    display_name: str
    start: datetime
    end: datetime
    business_unit_id: int
    facility: str
    locations: tuple[str, ...]
    capacity: int
    available: int
    waitlist: bool
    waiting: int
    bookable_earliest: datetime | None
    category: int = 40

    @property
    def location(self) -> str:
        return ", ".join(self.locations) or self.facility

    def rule_input(self) -> RuleInput:
        start = self.start.time().replace(tzinfo=None)
        return RuleInput(
            name=f"{self.product_name} · {self.start:%a %H:%M}"[:120],
            activity=self.product_name,
            product_name=self.product_name,
            weekday=self.start.weekday(),
            web_category_id=self.category,
            product_ids=[self.product_id] if self.product_id is not None else [],
            business_unit_ids=[self.business_unit_id],
            facility_contains=list(self.locations) or [self.facility],
            preferred_start_time=start,
            start_time=start,
            earliest_allowed_time=start,
            latest_allowed_time=start,
        )


def occurrence_fingerprint(item: ActivityOccurrence) -> str:
    identity = (
        item.product_id,
        item.product_name,
        item.business_unit_id,
        item.facility,
        item.locations,
        item.start.isoformat(),
        item.end.isoformat(),
        item.category,
    )
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def selection_key(rule) -> str:
    def normalized(values):
        return sorted(str(value).strip().casefold() for value in values)

    identity = (
        rule.booking_type,
        rule.web_category_id,
        (rule.product_name or rule.activity).strip().casefold(),
        rule.weekday,
        str(rule.start_time or rule.preferred_start_time or ""),
        normalized(rule.product_ids),
        normalized(rule.business_unit_ids),
        normalized(rule.facility_contains),
    )
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def same_occurrence_rule(rule, occurrence: ActivityOccurrence) -> bool:
    """Recognize existing manual rules with the engine's product/facility filters."""
    preferred = rule.start_time or rule.preferred_start_time
    if preferred and preferred != occurrence.start.time().replace(tzinfo=None):
        return False
    slot = GroupActivitySlot(
        id=occurrence.activity_id,
        name=occurrence.display_name,
        product_id=occurrence.product_id,
        product_name=occurrence.product_name,
        business_unit_id=occurrence.business_unit_id,
        business_unit_name=occurrence.facility,
        location_names=occurrence.locations,
        starts_at=occurrence.start,
        ends_at=occurrence.end,
        total_slots=occurrence.capacity,
        total_bookable=occurrence.capacity,
        left_to_book=occurrence.available,
        has_waiting_list=occurrence.waitlist,
        in_waiting_list=occurrence.waiting,
        bookable_earliest=occurrence.bookable_earliest,
        raw={},
    )
    domain = domain_rule(rule)
    return (
        rule.booking_type == "group_activity"
        and _group_activity_matches_rule(slot, domain)
        and _slot_in_allowed_window(slot, domain)
    )


class ActivityDiscovery:
    def __init__(self, gateway, timezone: str, *, ttl: float = 60):
        self.gateway = gateway
        self.timezone = ZoneInfo(timezone)
        self.ttl = ttl
        self.cache: OrderedDict[tuple, tuple[float, tuple[ActivityOccurrence, ...]]] = OrderedDict()
        self.lock = threading.Lock()

    def upcoming(self, account: CampushallenAccount, start: date, days: int = 7):
        if not account.enabled or not account.auth_state or account.requires_reconnect:
            raise ReconnectRequired("Connect your Campushallen account first.")
        today = datetime.now(self.timezone).date()
        if not (
            1 <= days <= 21
            and today <= start
            and start + timedelta(days=days) <= today + timedelta(days=22)
        ):
            raise ValueError("Choose a date within the next 21 days and a range of 1–21 days.")
        key = (account.id, hashlib.sha256(account.auth_state.encode()).hexdigest(), start, days)
        now = clock.monotonic()
        with self.lock:
            cached = self.cache.get(key)
            if cached and now - cached[0] < self.ttl:
                self.cache.move_to_end(key)
                return cached[1]
        # The live API rejects long periods with 403 despite accepting the token.
        # Use at most three weekly reads, never a request per keystroke or product.
        lower = datetime.combine(start, time.min, self.timezone)
        upper = datetime.combine(start + timedelta(days=days), time.min, self.timezone)
        try:
            with self.gateway.client(account) as (client, _):
                raw = []
                cursor = lower
                while cursor < upper:
                    chunk_end = min(cursor + timedelta(days=7), upper)
                    raw.extend(
                        client.group_activities(cursor, chunk_end - timedelta(milliseconds=1))
                    )
                    if len(raw) > 1000:
                        raise DiscoveryUnavailable(
                            "Too many activities. Choose a shorter date range."
                        )
                    cursor = chunk_end
        except (ValueError, TypeError, KeyError):
            raise DiscoveryUnavailable("Invalid schedule response") from None
        occurrences = {}
        current = datetime.now(self.timezone)
        for item in raw:
            try:
                duration = item.get("duration") or {}
                timestamps = [duration["start"], duration["end"]]
                if item.get("bookableEarliest"):
                    timestamps.append(item["bookableEarliest"])
                if any(
                    datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None
                    for value in timestamps
                ):
                    continue
                slot = _parse_group_activity_slot(item, self.timezone)
                if slot is None or not max(lower, current) <= slot.starts_at < upper:
                    continue
                occurrences[slot.id] = ActivityOccurrence(
                    slot.id,
                    slot.product_id,
                    slot.product_name or slot.name,
                    slot.name,
                    slot.starts_at,
                    slot.ends_at,
                    slot.business_unit_id or 1,
                    slot.business_unit_name or "",
                    slot.location_names,
                    slot.total_bookable or slot.total_slots,
                    slot.left_to_book,
                    slot.has_waiting_list,
                    slot.in_waiting_list,
                    slot.bookable_earliest,
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        result = tuple(
            sorted(occurrences.values(), key=lambda value: (value.start, value.activity_id))
        )
        with self.lock:
            self.cache[key] = (clock.monotonic(), result)
            self.cache.move_to_end(key)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return result


def search_occurrences(occurrences, query: str = "", facility: str = ""):
    words = query.casefold().split()
    return [
        item
        for item in occurrences
        if all(
            word
            in f"{item.product_name} {item.display_name} {item.facility} {item.location}".casefold()
            for word in words
        )
        and (
            not facility or facility.casefold() in (item.facility + " " + item.location).casefold()
        )
    ]
