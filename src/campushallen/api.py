from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .config import AppConfig
from .nettrace import body_shape, sanitize_headers, sanitize_url
from .schedule import BookingRule, BookingState, ClassSession

API_BASE_URL = "https://campushallen.brpsystems.com/brponline/api/ver3"
REQUEST_SOURCE = "mobilityweb"
SERVICE_WEB_CATEGORY_ID = 10
GROUP_ACTIVITY_WEB_CATEGORY_ID = 40
APP_ID = 238


@dataclass(frozen=True)
class ApiProbeResult:
    method: str
    url: str
    status_code: int
    content_type: str | None
    server_date: str | None
    response_shape: Any


@dataclass(frozen=True)
class ServiceProduct:
    id: int
    name: str
    business_unit_ids: tuple[int, ...]
    duration_minutes: int | None


@dataclass(frozen=True)
class ServiceSlot:
    product: ServiceProduct
    business_unit_id: int
    starts_at: datetime
    ends_at: datetime
    available_slots: int
    price_amount: int | None
    price_currency: str | None

    def to_session(self, activity: str) -> ClassSession:
        status = BookingState.BOOKABLE if self.available_slots > 0 else BookingState.FULL
        price = ""
        if self.price_amount is not None and self.price_currency:
            price = f" price={self.price_amount / 100:.0f} {self.price_currency}"
        return ClassSession(
            activity=activity,
            starts_at=self.starts_at,
            status=status,
            title=self.product.name,
            location=f"businessUnit={self.business_unit_id}",
            element_ref=f"service:{self.product.id}:{self.business_unit_id}:{self.starts_at.isoformat()}",
            raw_text=(
                f"{self.product.name} {self.starts_at:%Y-%m-%d %H:%M} "
                f"availableSlots={self.available_slots}{price}"
            ),
        )


@dataclass(frozen=True)
class GroupActivitySlot:
    id: int
    name: str
    product_id: int | None
    product_name: str | None
    business_unit_id: int
    business_unit_name: str | None
    location_names: tuple[str, ...]
    starts_at: datetime
    ends_at: datetime
    total_slots: int
    total_bookable: int
    left_to_book: int
    has_waiting_list: bool
    in_waiting_list: int
    bookable_earliest: datetime | None
    raw: dict[str, Any]

    def to_session(self, activity: str) -> ClassSession:
        if self.left_to_book > 0:
            status = BookingState.BOOKABLE
        elif self.has_waiting_list:
            status = BookingState.WAITLIST
        else:
            status = BookingState.FULL
        return ClassSession(
            activity=activity,
            starts_at=self.starts_at,
            status=status,
            title=self.name,
            location=self.facility_text,
            element_ref=f"groupactivity:{self.id}",
            raw_text=(
                self.raw_text
            ),
        )

    @property
    def facility_text(self) -> str:
        names = [self.business_unit_name or f"businessUnit={self.business_unit_id}", *self.location_names]
        return ", ".join(name for name in names if name)

    @property
    def raw_text(self) -> str:
        return (
            f"{self.name} id={self.id} {self.starts_at:%Y-%m-%d %H:%M} "
            f"facility={self.facility_text} slots={self.left_to_book}/{self.total_bookable} "
            f"total={self.total_slots} waitlist={self.in_waiting_list if self.has_waiting_list else 0}"
        )


@dataclass(frozen=True)
class GroupActivitySelection:
    state: BookingState
    slot: GroupActivitySlot | None
    candidates: tuple[GroupActivitySlot, ...]
    message: str


class CampushallenHttpClient:
    """HTTP client for Campushallen's Go Active/BRP API."""

    def __init__(self, config: AppConfig, *, load_saved_auth: bool = True) -> None:
        self.config = config
        self.client = httpx.Client(
            base_url=API_BASE_URL,
            follow_redirects=True,
            timeout=30.0,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
                "Referer": config.base_url,
                "X-REQUEST-SOURCE": REQUEST_SOURCE,
                "User-Agent": "Mozilla/5.0 CampushallenBookingBot/0.1",
            },
        )
        if load_saved_auth and config.auth.storage_state_path.exists():
            self._load_storage_state(config.auth.storage_state_path)
        if load_saved_auth and config.auth.token_state_path.exists():
            self._load_token_state(config.auth.token_state_path)

    def _load_storage_state(self, storage_state_path: Path) -> None:
        raw = json.loads(storage_state_path.read_text(encoding="utf-8"))
        for cookie in raw.get("cookies", []):
            domain = cookie.get("domain")
            if not domain:
                continue
            self.client.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=domain.lstrip("."),
                path=cookie.get("path", "/"),
            )

    def _load_token_state(self, token_state_path: Path) -> None:
        raw = json.loads(token_state_path.read_text(encoding="utf-8"))
        access_token = raw.get("access_token")
        if access_token:
            self.client.headers["Authorization"] = f"Bearer {access_token}"

    def get(self, url: str, params: dict[str, Any] | None = None) -> ApiProbeResult:
        response = self.client.get(url, params=params)
        return self._result("GET", str(response.url), response)

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        if access_token:
            self.client.headers["Authorization"] = f"Bearer {access_token}"
        return data

    def validate_token(self, token: str) -> dict[str, Any]:
        response = self.client.post("/auth/validate", headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        response = self.client.get(f"/customers/{customer_id}")
        response.raise_for_status()
        return response.json()

    def customer_group_activity_bookings(self, customer_id: int, start_time_point: datetime | None = None) -> list[dict[str, Any]]:
        params = {"startTimePoint": _api_datetime(start_time_point)} if start_time_point else None
        response = self.client.get(f"/customers/{customer_id}/bookings/groupactivities", params=params)
        response.raise_for_status()
        return list(response.json())

    def book_group_activity(
        self,
        customer_id: int,
        group_activity_id: int,
        *,
        allow_waiting_list: bool = False,
        recaptcha_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "groupActivity": group_activity_id,
            "allowWaitingList": allow_waiting_list,
        }
        if recaptcha_token:
            body["recaptchaToken"] = recaptcha_token
        response = self.client.post(f"/customers/{customer_id}/bookings/groupactivities", json=body)
        self.booking_http_status = response.status_code
        response.raise_for_status()
        return response.json()

    def cancel_group_activity_booking(
        self,
        customer_id: int,
        booking_id: int,
        booking_type: str,
        *,
        try_to_refund: bool = False,
        allow_late_cancellation_with_no_show: bool | None = None,
        recaptcha_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"bookingType": booking_type}
        if try_to_refund:
            params["tryToRefund"] = "true"
        if allow_late_cancellation_with_no_show is not None:
            params["allowLateCancellationWithNoShow"] = str(allow_late_cancellation_with_no_show).lower()
        if recaptcha_token:
            params["recaptchaToken"] = recaptcha_token
        response = self.client.delete(f"/customers/{customer_id}/bookings/groupactivities/{booking_id}", params=params)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {"status": response.status_code}

    def service_products(self, web_category_id: int = SERVICE_WEB_CATEGORY_ID) -> list[ServiceProduct]:
        response = self.client.get("/products/services", params={"webCategory": web_category_id})
        response.raise_for_status()
        products: list[ServiceProduct] = []
        for item in response.json():
            business_units = tuple(
                int(unit["id"]) for unit in item.get("businessUnits", []) if unit.get("id") is not None
            )
            products.append(
                ServiceProduct(
                    id=int(item["id"]),
                    name=str(item["name"]),
                    business_unit_ids=business_units,
                    duration_minutes=item.get("time"),
                )
            )
        return products

    def service_suggestions(
        self,
        product_id: int,
        business_unit_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/products/services/{product_id}/suggestions",
            params={
                "businessUnit": business_unit_id,
                "period.start": _api_datetime(period_start),
                "period.end": _api_datetime(period_end),
                "renderPrice": "true",
            },
        )
        response.raise_for_status()
        return list(response.json())

    def group_activities(
        self,
        period_start: datetime,
        period_end: datetime,
        *,
        web_category_id: int = GROUP_ACTIVITY_WEB_CATEGORY_ID,
        business_unit_id: int = 1,
    ) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/apps/{APP_ID}/groupactivities",
            params={
                "businessUnit": business_unit_id,
                "groupActivitiesFor": "web",
                "period.start": _api_datetime(period_start),
                "period.end": _api_datetime(period_end),
                "webCategory": web_category_id,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError("Invalid group activity response shape")
        return payload

    def _result(self, method: str, url: str, response: httpx.Response) -> ApiProbeResult:
        content_type = response.headers.get("content-type")
        return ApiProbeResult(
            method=method,
            url=sanitize_url(url),
            status_code=response.status_code,
            content_type=content_type,
            server_date=response.headers.get("date"),
            response_shape=body_shape(response.text, content_type),
        )

    def close(self) -> None:
        self.client.close()


def find_service_slots(client: CampushallenHttpClient, rule: BookingRule, target_start: datetime) -> list[ServiceSlot]:
    products = [
        product
        for product in client.service_products(rule.web_category_id)
        if _product_matches_rule(product, rule)
    ]
    day_start = target_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1) - timedelta(milliseconds=1)
    slots: list[ServiceSlot] = []
    for product in products:
        business_unit_ids = rule.business_unit_ids or product.business_unit_ids
        for business_unit_id in business_unit_ids:
            if product.business_unit_ids and business_unit_id not in product.business_unit_ids:
                continue
            for item in client.service_suggestions(product.id, business_unit_id, day_start, day_end):
                parsed = _parse_service_slot(product, business_unit_id, item, target_start.tzinfo)
                if parsed:
                    slots.append(parsed)
    return sorted(slots, key=lambda slot: (slot.starts_at, slot.product.name, slot.business_unit_id))


def find_matching_service_slot(
    client: CampushallenHttpClient,
    rule: BookingRule,
    target_start: datetime,
) -> tuple[ServiceSlot | None, list[ServiceSlot]]:
    slots = find_service_slots(client, rule, target_start)
    matching = [
        slot
        for slot in slots
        if slot.starts_at.date() == target_start.date()
        and slot.starts_at.timetz().replace(tzinfo=None) == target_start.timetz().replace(tzinfo=None)
    ]
    return (matching[0] if matching else None, slots)


def find_group_activity_slots(
    client: CampushallenHttpClient,
    rule: BookingRule,
    target_date: date,
    tzinfo: Any,
) -> list[GroupActivitySlot]:
    day_start = datetime.combine(target_date, time.min, tzinfo)
    day_end = day_start + timedelta(days=1) - timedelta(milliseconds=1)
    business_unit_ids = rule.business_unit_ids or (1,)
    slots: list[GroupActivitySlot] = []
    for business_unit_id in business_unit_ids:
        for item in client.group_activities(
            day_start,
            day_end,
            web_category_id=rule.web_category_id,
            business_unit_id=business_unit_id,
        ):
            parsed = _parse_group_activity_slot(item, tzinfo)
            if parsed and _group_activity_matches_rule(parsed, rule):
                slots.append(parsed)
    return sorted(slots, key=lambda slot: (slot.starts_at, slot.name, slot.id))


def find_group_activity_slots_for_range(
    client: CampushallenHttpClient,
    rule: BookingRule,
    period_start: datetime,
    period_end: datetime,
    tzinfo: Any,
) -> list[GroupActivitySlot]:
    business_unit_ids = rule.business_unit_ids or (1,)
    slots: list[GroupActivitySlot] = []
    cursor = period_start
    while cursor <= period_end:
        day_start = datetime.combine(cursor.astimezone(tzinfo).date(), time.min, tzinfo)
        day_end = min(day_start + timedelta(days=1) - timedelta(milliseconds=1), period_end)
        for business_unit_id in business_unit_ids:
            for item in client.group_activities(
                day_start,
                day_end,
                web_category_id=rule.web_category_id,
                business_unit_id=business_unit_id,
            ):
                parsed = _parse_group_activity_slot(item, tzinfo)
                if parsed and _group_activity_matches_rule(parsed, rule):
                    slots.append(parsed)
        cursor = day_start + timedelta(days=1)
    return sorted(slots, key=lambda slot: (slot.starts_at, slot.name, slot.id))


def find_matching_group_activity_slot(
    client: CampushallenHttpClient,
    rule: BookingRule,
    target_start: datetime,
) -> tuple[GroupActivitySlot | None, list[GroupActivitySlot]]:
    slots = find_group_activity_slots(client, rule, target_start.date(), target_start.tzinfo)
    matching = [
        slot
        for slot in slots
        if slot.starts_at.date() == target_start.date()
        and slot.starts_at.timetz().replace(tzinfo=None) == target_start.timetz().replace(tzinfo=None)
    ]
    return (matching[0] if matching else None, slots)


def select_group_activity_for_release_date(
    client: CampushallenHttpClient,
    rule: BookingRule,
    run_date: date,
    now: datetime,
    lookahead_days: int,
    tzinfo: Any,
) -> GroupActivitySelection:
    period_start = datetime.combine(now.date(), time.min, tzinfo)
    period_end = period_start + timedelta(days=lookahead_days) - timedelta(milliseconds=1)
    candidates = tuple(find_group_activity_slots_for_range(client, rule, period_start, period_end, tzinfo))
    release_candidates = tuple(
        slot
        for slot in candidates
        if slot.bookable_earliest is not None
        and slot.bookable_earliest.astimezone(tzinfo).date() == run_date
    )
    if not release_candidates:
        return GroupActivitySelection(
            BookingState.TARGET_NOT_FOUND,
            None,
            candidates,
            (
                "TARGET_NOT_FOUND: no matching group activity has "
                f"bookableEarliest on {run_date.isoformat()}"
            ),
        )
    in_window = tuple(slot for slot in release_candidates if _slot_in_allowed_window(slot, rule))
    if not in_window:
        return GroupActivitySelection(
            BookingState.SUSPICIOUS_TARGET,
            None,
            release_candidates,
            "SUSPICIOUS_TARGET: release-date candidates exist but all are outside allowed_start_window",
        )
    if len(in_window) == 1:
        slot = in_window[0]
        return GroupActivitySelection(
            BookingState.BOOKABLE,
            slot,
            candidates,
            f"Selected release-date target {slot.raw_text}",
        )
    return GroupActivitySelection(
        BookingState.AMBIGUOUS_TARGET,
        None,
        in_window,
        (
            "AMBIGUOUS_TARGET: multiple matching group activities have "
            f"bookableEarliest on {run_date.isoformat()}"
        ),
    )


def select_group_activity_for_date(
    client: CampushallenHttpClient,
    rule: BookingRule,
    target_date: date,
    tzinfo: Any,
) -> GroupActivitySelection:
    candidates = tuple(find_group_activity_slots(client, rule, target_date, tzinfo))
    if not candidates:
        return GroupActivitySelection(
            BookingState.TARGET_NOT_FOUND,
            None,
            candidates,
            f"TARGET_NOT_FOUND: no {rule.product_name or rule.activity} group activity on {target_date.isoformat()}",
        )
    in_window = tuple(slot for slot in candidates if _slot_in_allowed_window(slot, rule))
    if not in_window:
        return GroupActivitySelection(
            BookingState.SUSPICIOUS_TARGET,
            None,
            candidates,
            "SUSPICIOUS_TARGET: candidates exist but all are outside allowed_start_window",
        )
    if len(in_window) == 1:
        slot = in_window[0]
        return GroupActivitySelection(BookingState.BOOKABLE, slot, candidates, f"Selected {slot.raw_text}")
    preferred = _preferred_start_matches(in_window, rule)
    if len(preferred) == 1:
        slot = preferred[0]
        return GroupActivitySelection(BookingState.BOOKABLE, slot, candidates, f"Selected preferred-time {slot.raw_text}")
    return GroupActivitySelection(
        BookingState.AMBIGUOUS_TARGET,
        None,
        candidates,
        "AMBIGUOUS_TARGET: multiple plausible candidates matched",
    )


def save_token_state(config: AppConfig, token_data: dict[str, Any]) -> Path:
    config.auth.token_state_path.parent.mkdir(parents=True, exist_ok=True)
    expires_at = 0
    if token_data.get("expires_in"):
        expires_at = int(datetime.now(timezone.utc).timestamp() + int(token_data["expires_in"]))
    safe_data = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": expires_at,
        "customer_id": _customer_id_from_login(token_data),
    }
    config.auth.token_state_path.write_text(json.dumps(safe_data, indent=2), encoding="utf-8")
    config.auth.token_state_path.chmod(0o600)
    return config.auth.token_state_path


def _customer_id_from_login(token_data: dict[str, Any]) -> int | None:
    username = token_data.get("username")
    try:
        return int(username)
    except (TypeError, ValueError):
        return None


def _api_datetime(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_service_slot(
    product: ServiceProduct,
    business_unit_id: int,
    item: dict[str, Any],
    tzinfo: Any,
) -> ServiceSlot | None:
    duration = item.get("duration") or {}
    start_raw = duration.get("start")
    end_raw = duration.get("end")
    if not start_raw or not end_raw:
        return None
    starts_at = _parse_api_datetime(start_raw).astimezone(tzinfo)
    ends_at = _parse_api_datetime(end_raw).astimezone(tzinfo)
    price = item.get("price") or {}
    return ServiceSlot(
        product=product,
        business_unit_id=business_unit_id,
        starts_at=starts_at,
        ends_at=ends_at,
        available_slots=int(item.get("availableSlots") or 0),
        price_amount=price.get("amount"),
        price_currency=price.get("currency"),
    )


def _parse_group_activity_slot(item: dict[str, Any], tzinfo: Any) -> GroupActivitySlot | None:
    duration = item.get("duration") or {}
    start_raw = duration.get("start")
    end_raw = duration.get("end")
    if not start_raw or not end_raw:
        return None
    product = item.get("groupActivityProduct") or {}
    business_unit = item.get("businessUnit") or {}
    slots = item.get("slots") or {}
    bookable_earliest = item.get("bookableEarliest")
    return GroupActivitySlot(
        id=int(item["id"]),
        name=str(item.get("name") or product.get("name") or item["id"]),
        product_id=int(product["id"]) if product.get("id") is not None else None,
        product_name=product.get("name"),
        business_unit_id=int(business_unit.get("id") or 0),
        business_unit_name=business_unit.get("name"),
        location_names=tuple(str(location.get("name")) for location in item.get("locations", []) if location.get("name")),
        starts_at=_parse_api_datetime(start_raw).astimezone(tzinfo),
        ends_at=_parse_api_datetime(end_raw).astimezone(tzinfo),
        total_slots=int(slots.get("total") or 0),
        total_bookable=int(slots.get("totalBookable") or 0),
        left_to_book=int(slots.get("leftToBook") or 0),
        has_waiting_list=bool(slots.get("hasWaitingList")),
        in_waiting_list=int(slots.get("inWaitingList") or 0),
        bookable_earliest=(
            _parse_api_datetime(bookable_earliest).astimezone(tzinfo)
            if bookable_earliest
            else None
        ),
        raw=item,
    )


def _parse_api_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _product_matches_rule(product: ServiceProduct, rule: BookingRule) -> bool:
    if rule.product_ids and product.id not in rule.product_ids:
        return False
    if not rule.product_ids and rule.product_name_contains:
        name = product.name.casefold()
        return any(fragment.casefold() in name for fragment in rule.product_name_contains)
    return True


def _group_activity_matches_rule(slot: GroupActivitySlot, rule: BookingRule) -> bool:
    if rule.product_ids and slot.product_id not in rule.product_ids:
        return False
    if slot.starts_at.weekday() != rule.weekday:
        return False
    if rule.product_name:
        if not _strong_product_match(slot, rule.product_name):
            return False
    elif rule.product_name_contains:
        text = f"{slot.name} {slot.product_name or ''}".casefold()
        if not any(fragment.casefold() in text for fragment in rule.product_name_contains):
            return False
    elif rule.activity.casefold() not in f"{slot.name} {slot.product_name or ''}".casefold():
        return False
    if rule.facility_contains:
        facility_text = f"{slot.facility_text} {slot.name} {slot.product_name or ''}".casefold()
        if not all(fragment.casefold() in facility_text for fragment in rule.facility_contains):
            return False
    return True


def _strong_product_match(slot: GroupActivitySlot, product_name: str) -> bool:
    target = _normalize_name(product_name)
    names = [_normalize_name(slot.name), _normalize_name(slot.product_name or "")]
    return any(name == target or name.startswith(f"{target} ") for name in names)


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _slot_in_allowed_window(slot: GroupActivitySlot, rule: BookingRule) -> bool:
    local_time = slot.starts_at.timetz().replace(tzinfo=None)
    if rule.allowed_start_earliest and local_time < rule.allowed_start_earliest:
        return False
    if rule.allowed_start_latest and local_time > rule.allowed_start_latest:
        return False
    return True


def _preferred_start_matches(
    slots: tuple[GroupActivitySlot, ...],
    rule: BookingRule,
) -> tuple[GroupActivitySlot, ...]:
    if not rule.preferred_start_time:
        return ()
    return tuple(
        slot
        for slot in slots
        if slot.starts_at.timetz().replace(tzinfo=None) == rule.preferred_start_time
    )


def sanitized_request_summary(method: str, url: str, headers: dict[str, str], body: str | None = None) -> dict[str, Any]:
    content_type = headers.get("content-type")
    return {
        "method": method,
        "url": sanitize_url(url),
        "headers": sanitize_headers(headers),
        "body_shape": body_shape(body, content_type),
    }


def absolute_url(config: AppConfig, url: str) -> str:
    return url if url.startswith(("http://", "https://")) else urljoin(config.base_url, url)
