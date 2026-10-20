from pydantic import ValidationError

from campushallen.database.models import CampushallenAccount, utcnow
from campushallen.services.invitations import invite_status as domain_invite_status
from campushallen.services.secrets import ReconnectRequired, TokenVault
from campushallen.web.i18n import gettext as _
from campushallen.web.i18n import gettext_noop as N_


def connection_status(account: CampushallenAccount, vault: TokenVault | None = None) -> str:
    if not account.enabled:
        return _("Disabled")
    if account.auth_status in ("authentication_error", "reauth_failed"):
        return _("Authentication error")
    if account.requires_reconnect or account.auth_status == "reconnect_required":
        return _("Reconnection required")
    if account.auth_status == "token_expired":
        return _("Token expired; reconnecting")
    if account.auth_state and vault:
        try:
            state = vault.open(account.auth_state)
            if state.get("expires_at") and float(state["expires_at"]) <= utcnow().timestamp():
                return _("Token expired; reconnection needed")
        except (ReconnectRequired, ValueError, TypeError):
            return _("Reconnection required")
    return _("Connected") if account.auth_state else _("Not connected")


OUTCOME_LABELS = {
    "BOOKED": N_("Booked"),
    "ALREADY_BOOKED": N_("Already booked"),
    "TARGET_NOT_FOUND": N_("No matching activity found"),
    "AMBIGUOUS_TARGET": N_("Several possible activities found"),
    "SUSPICIOUS_TARGET": N_("Activity could not be verified safely"),
    "SKIPPED_WAITLIST_ONLY": N_("Only waiting-list spots available"),
    "AUTH_FAILED": N_("Campushallen sign-in failed"),
    "BOOKING_FAILED": N_("Booking failed"),
    "VERIFICATION_FAILED": N_("Booking could not be verified"),
    "DRY_RUN": N_("Dry run – no booking made"),
    "NOT_OPEN": N_("Booking has not opened yet"),
    "DISABLED": N_("Disabled"),
    "CLAIMED": N_("Booking is already being processed"),
    "UNSUPPORTED": N_("Not supported"),
}


def friendly_result(code: str) -> str:
    return _(OUTCOME_LABELS[code]) if code in OUTCOME_LABELS else _("Unknown result")


def verification_label(code: str) -> str:
    if code in ("BOOKED", "ALREADY_BOOKED"):
        return _("Confirmed by independent bookings check")
    if code == "VERIFICATION_FAILED":
        return _("Unconfirmed; check Campushallen before any retry")
    if code == "CLAIMED":
        return _("Pending or interrupted; inspect later results")
    return _("No booking confirmed by this attempt")


STATUS_LABELS = {
    "connected": N_("Connected"),
    "disconnected": N_("Disconnected"),
    "reconnect_required": N_("Reconnection required"),
    "authentication_error": N_("Authentication error"),
    "reauth_failed": N_("Campushallen sign-in failed"),
    "token_expired": N_("Token expired; reconnecting"),
    "RECONNECT_REQUIRED": N_("Reconnection required"),
    "REAUTH_FAILED": N_("Campushallen sign-in failed"),
    "TEMPORARY_AUTH_FAILURE": N_("Temporary connection problem"),
}


def status_label(code: str | None) -> str:
    if not code:
        return _("None")
    label = STATUS_LABELS.get(code) or OUTCOME_LABELS.get(code)
    return _(label) if label else _("Check the connection")


INVITE_LABELS = {
    "Used": N_("Used"),
    "Revoked": N_("Revoked"),
    "Expired": N_("Expired"),
    "Available": N_("Available"),
}


def invite_status(invitation) -> str:
    return _(INVITE_LABELS[domain_invite_status(invitation)])


BACKUP_LABELS = {
    N_("Backup successful"),
    N_("Backup failed"),
    N_("Backup overdue"),
    N_("Backup status unavailable"),
}


WORKER_LABELS = {
    "Worker healthy": N_("Worker healthy"),
    "Worker hasn't reported recently": N_("Worker hasn't reported recently"),
    "Worker disabled / not running": N_("Worker disabled / not running"),
}


def worker_label(status: str) -> str:
    return (
        _(WORKER_LABELS[status]) if status in WORKER_LABELS else _("Worker disabled / not running")
    )


SAFE_ERRORS = {
    N_("At least one active admin must remain."),
    N_("Choose a different username."),
    N_("Earliest time must be before latest time"),
    N_("Login state changed; sign in again."),
    N_("Name and activity must not be blank"),
    N_("Password changed in another session; log in again."),
    N_("Rule times must be local clock times without a timezone offset"),
    N_("Service rules require a fixed start time"),
    N_("Start time must be inside the allowed window"),
    N_("The last active admin cannot be deleted. Create or promote another admin first."),
    N_("The new website passwords do not match."),
    N_("This invitation is unavailable. Ask the owner for a new invitation."),
    N_("Type DELETE to confirm local account removal."),
    N_("Type DELETE to confirm local data removal."),
    N_("Use a label of at most 120 characters and a future expiry."),
    N_("Use a username of 1–254 characters without spaces or control characters."),
    N_("Use a website password of 12–4096 characters."),
    N_("Website password is incorrect."),
    N_("Website passwords do not match."),
}


def safe_error(message: str) -> str:
    return (
        _(message)
        if message in SAFE_ERRORS
        else _("Unable to complete the request. Check the form and try again.")
    )


REASONS = {
    N_("Live operation phase recorded; inspect verification outcome."),
    N_("A previous live attempt exists; inspect booking history before any manual retry."),
    N_("API booking opening time has not arrived."),
    N_("API did not supply bookableEarliest; no booking sent."),
    N_("Account was removed"),
    N_("Account, user, or rule is disabled."),
    N_("Activity discovery failed; try again later."),
    N_("Activity has already started."),
    N_("Activity is already booked."),
    N_("Booking could not be completed."),
    N_("Booking independently verified."),
    N_("Campushallen request failed; no booking confirmed."),
    N_("Dry-run; no booking sent."),
    N_("Fixed start time no longer matches."),
    N_("Live request reserved; an interrupted request requires manual verification."),
    N_("Matching activity discovered."),
    N_("No regular spots available; no booking sent."),
    N_("No safe upcoming target with API bookableEarliest was found."),
    N_("No unique safe target was selected."),
    N_("Queued target changed; no booking sent."),
    N_("Reconnect this Campushallen account."),
    N_("Request outcome is uncertain; check Campushallen before retrying."),
    N_("Request sent but booking could not be verified. Check Campushallen before retrying."),
    N_("Selection is ambiguous or outside the allowed time window."),
    N_("Service booking is available only through the legacy CLI."),
    N_("Service rules remain available through the legacy CLI; web worker does not book services."),
}


def reason(message: str) -> str:
    return (
        _(message)
        if message in REASONS
        else _("See the result and check Campushallen if you are unsure.")
    )


EVENT_LABELS = {
    "LIVE_BOOKING_ENABLED": N_("Real booking activated"),
    "LIVE_BOOKING_DISABLED": N_("Real booking stopped"),
    "WEBSITE_LOGIN_FAILED": N_("Website sign-in failed"),
    "WEBSITE_LOGIN_LOCKED": N_("Website sign-in temporarily locked"),
    "WEBSITE_LOGIN_SUCCESS": N_("Website sign-in successful"),
    "WEBSITE_LOGIN_LOCK_EXPIRED": N_("Website sign-in lock expired"),
    "USER_REGISTERED": N_("User registered"),
    "USER_CREATED": N_("User created"),
    "USER_DELETED": N_("User deleted"),
    "ADMIN_PROMOTED": N_("Admin promoted"),
    "ADMIN_DEMOTED": N_("Admin demoted"),
    "LOGIN_SUCCESS": N_("Login success"),
    "LOGIN_FAILED": N_("Login failed"),
    "LOGOUT": N_("Logout"),
    "SESSIONS_REVOKED": N_("Sessions revoked"),
    "PASSWORD_CHANGED": N_("Password changed"),
    "CAMPUSHALLEN_CONNECTED": N_("Campushallen connected"),
    "CAMPUSHALLEN_RECONNECTED": N_("Campushallen reconnected"),
    "ACCOUNT_DISABLED": N_("Account disabled"),
    "ACCOUNT_ENABLED": N_("Account enabled"),
    "ACCOUNT_DISCONNECTED": N_("Account disconnected"),
    "ACCOUNT_DELETED": N_("Account deleted"),
    "STORED_CREDENTIAL_ENABLED": N_("Stored credential enabled"),
    "STORED_CREDENTIAL_REMOVED": N_("Stored credential removed"),
    "STORED_CREDENTIAL_REPLACED": N_("Stored credential replaced"),
    "BOOKING_RULE_CREATED": N_("Booking rule created"),
    "BOOKING_RULE_UPDATED": N_("Booking rule updated"),
    "BOOKING_RULE_DELETED": N_("Booking rule deleted"),
    "INVITE_CREATED": N_("Invite created"),
    "INVITE_USED": N_("Invite used"),
    "INVITE_REVOKED": N_("Invite revoked"),
    "TOKEN_EXPIRED": N_("Token expired"),
    "REAUTH_SUCCESS": N_("Reauth success"),
    "REAUTH_FAILED": N_("Reauth failed"),
    "RECONNECT_REQUIRED": N_("Reconnect required"),
}


def event_label(code: str) -> str:
    return _(EVENT_LABELS[code]) if code in EVENT_LABELS else _("Security event")


def rule_errors(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors(include_input=False):
        # Domain validators provide known safe messages; Pydantic metadata never reaches HTML.
        domain_error = error.get("ctx", {}).get("error")
        if domain_error and str(domain_error) in SAFE_ERRORS:
            messages.append(safe_error(str(domain_error)))
            continue
        location = error["loc"]
        field = str(location[0]) if location else ""
        if field in ("name", "activity", "product_name"):
            messages.append(
                _("Enter a rule name (1–120 characters) and an activity (1–200 characters).")
            )
        elif field == "weekday":
            messages.append(_("Choose a weekday from Monday to Sunday."))
        elif field == "booking_type":
            messages.append(_("Choose a valid booking type."))
        elif field in ("product_ids", "business_unit_ids", "web_category_id"):
            messages.append(_("Enter numeric IDs. The category ID must be greater than zero."))
        elif field == "facility_contains":
            messages.append(_("Use at most 30 facility filters."))
        else:
            messages.append(_("Enter valid local times, for example 18:45, without a timezone."))
    return " ".join(dict.fromkeys(messages))


PHASE_LABELS = {
    "PREBOOK_AUTH_FAILED": N_("Authentication failed before booking"),
    "BOOK_POST_STARTED": N_("Booking request started; result unknown"),
    "BOOK_POST_AUTH_REJECTED": N_("Booking request rejected authentication"),
    "BOOK_POST_REJECTED": N_("Booking request rejected"),
    "BOOK_POST_TIMEOUT_OR_UNCERTAIN": N_("Booking request outcome uncertain"),
    "BOOK_POST_ACCEPTED": N_("Booking response received; verification required"),
    "VERIFY_AUTH_FAILED": N_("Verification authentication failed"),
    "VERIFY_FAILED": N_("Independent verification did not confirm booking"),
    "VERIFIED_BOOKED": N_("Booking independently confirmed"),
    "VERIFIED_ALREADY_BOOKED": N_("Existing booking independently confirmed"),
}


def phase_label(code: str | None) -> str:
    return _(PHASE_LABELS[code]) if code in PHASE_LABELS else _("Unknown result")
