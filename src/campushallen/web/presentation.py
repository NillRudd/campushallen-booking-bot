from campushallen.database.models import CampushallenAccount


def connection_status(account: CampushallenAccount) -> str:
    if not account.enabled:
        return "Disabled"
    if account.auth_status in ("authentication_error", "reauth_failed"):
        return "Authentication error"
    if account.requires_reconnect or account.auth_status == "reconnect_required":
        return "Reconnection required"
    if account.auth_status == "token_expired":
        return "Token expired; reconnecting"
    return "Connected" if account.auth_state else "Not connected"


def friendly_result(code: str) -> str:
    labels = {
        "DRY_RUN": "Preview only",
        "BOOKED": "Booked",
        "ALREADY_BOOKED": "Already booked",
        "SKIPPED_WAITLIST_ONLY": "Waiting list skipped",
        "AUTH_FAILED": "Reconnection needed",
        "VERIFICATION_FAILED": "Booking needs manual verification",
        "CLAIMED": "Live attempt reserved",
    }
    return labels.get(code, code.replace("_", " ").capitalize())


def verification_label(code: str) -> str:
    if code in ("BOOKED", "ALREADY_BOOKED"):
        return "Confirmed by independent bookings check"
    if code == "VERIFICATION_FAILED":
        return "Unconfirmed; check Campushallen before any retry"
    if code == "CLAIMED":
        return "Pending or interrupted; inspect later results"
    return "No booking confirmed by this attempt"
