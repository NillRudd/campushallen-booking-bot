"""Discard provider prose/parameters; retain only known public rejection enums.

The official Go Active frontend reads response.data.errorCode (and a free-form
rule), not HTTP status alone. Its public Swedish error catalog supplies these
codes. A recognized code explains a rejection; it never authorizes a POST retry
or proves that no mutation happened. Independent verification remains mandatory.
"""

import json
from dataclasses import dataclass

import httpx

ERROR_CATEGORIES = {
    "TOO_MANY_CONCURRENT_BOOKINGS": "BOOKING_LIMIT_REACHED",
    "TOO_MANY_CONCURRENT_WAITING_LISTS": "WAITLIST_RESTRICTION",
    "TOO_EARLY_TO_BOOK": "NOT_BOOKABLE_YET",
    "TOO_LATE_TO_BOOK": "BOOKING_WINDOW_CLOSED",
    "TOO_LATE_TO_BOOK_WAITING_LIST": "WAITLIST_RESTRICTION",
    "BOOKING_REQUIRES_MEMBERSHIP": "MEMBERSHIP_RESTRICTION",
    "BOOKING_NOT_ALLOWED": "BOOKING_RIGHTS_RESTRICTION",
    "BOOKING_RULE_PREVENTS_BOOKING": "PROVIDER_BOOKING_RULE",
    "BOOKING_RULE_PREVENTS_ADDING_TO_WAITING_LIST": "WAITLIST_RESTRICTION",
    "BOOKING_WAITING_LIST_NOT_ALLOWED_IF_FREE_SLOTS": "WAITLIST_RESTRICTION",
    "WAITING_LIST_NOT_ALLOWED_FOR_PAID_PARTICIPANTS": "WAITLIST_RESTRICTION",
    "BOOKING_TERMS_NOT_ACCEPTED": "BOOKING_TERMS_REQUIRED",
    "TOO_MANY_NOSHOWS": "NO_SHOW_RESTRICTION",
    "ALREADY_BOOKED": "ALREADY_BOOKED",
    "ALREADY_ON_WAITING_LIST": "ALREADY_WAITLISTED",
    "BOOKING_CLASHES_WITH_OTHER_BOOKING": "BOOKING_CONFLICT",
    "CANNOT_BOOK_CANCELLED_ACTIVITY": "ACTIVITY_CANCELLED",
    "INVALID_INPUT": "INVALID_BOOKING_INPUT",
    "USER_CREDENTIALS_ARE_INVALID": "AUTH_REJECTED",
    "LOGIN_UNSUCCESSFUL_INVALID_CREDENTIALS": "AUTH_REJECTED",
}


@dataclass(frozen=True)
class ProviderRejection:
    category: str = "UNKNOWN_PROVIDER_REJECTION"
    code: str = "UNRECOGNIZED"

    @property
    def message(self) -> str:
        return f"Provider rejection: {self.category}; errorCode={self.code}."


def provider_rejection(response: httpx.Response) -> ProviderRejection:
    """Never copy a message, rule, customer, field value, header, or unknown code.

    Bound JSON parsing as well as output. HTML, malformed/oversized/deep JSON and
    unknown shapes produce one fixed message, including for generic 401/403.
    """
    if not 400 <= response.status_code < 500:
        return ProviderRejection()
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return ProviderRejection()
    if len(response.content) > 16_384:
        return ProviderRejection()
    try:
        data = json.loads(response.content)
    except (ValueError, RecursionError):
        return ProviderRejection()
    code = data.get("errorCode") if isinstance(data, dict) else None
    if not isinstance(code, str) or code not in ERROR_CATEGORIES:
        return ProviderRejection()
    return ProviderRejection(ERROR_CATEGORIES[code], code)


# Exact message matching also prevents untrusted historical text reaching the UI.
SAFE_REJECTION_MESSAGES = {
    ProviderRejection(category, code).message: ProviderRejection(category, code)
    for code, category in ERROR_CATEGORIES.items()
}
SAFE_REJECTION_MESSAGES[ProviderRejection().message] = ProviderRejection()
