from campushallen.database.models import CampushallenAccount, utcnow
from campushallen.services.secrets import ReconnectRequired, TokenVault


def connection_status(account: CampushallenAccount, vault: TokenVault | None = None) -> str:
    if not account.enabled:
        return "Disabled"
    if account.auth_status in ("authentication_error", "reauth_failed"):
        return "Authentication error"
    if account.requires_reconnect or account.auth_status == "reconnect_required":
        return "Reconnection required"
    if account.auth_status == "token_expired":
        return "Token expired; reconnecting"
    if account.auth_state and vault:
        try:
            state = vault.open(account.auth_state)
            if state.get("expires_at") and float(state["expires_at"]) <= utcnow().timestamp():
                return "Token expired; reconnection needed"
        except (ReconnectRequired, ValueError, TypeError):
            return "Reconnection required"
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
