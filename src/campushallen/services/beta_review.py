"""Fresh read-only prerequisites for explicit per-connection activation."""

from sqlalchemy import select

from campushallen.database.models import BookingAttempt, BookingClaim, BookingRule, Outcome

from .credentials import credential_for
from .identities import identity_status
from .secrets import ReconnectRequired


def review_connection(db, account, engine, credentials):
    errors = []
    if not account.enabled or account.requires_reconnect or not account.auth_state:
        errors.append("Connect and enable the account before activation.")
    identity_error = identity_status(db, account, engine.gateway.vault)
    if identity_error:
        errors.append(identity_error)
    credential = credential_for(db, account.id)
    try:
        if not credential:
            raise ReconnectRequired("Missing credential")
        password = credentials.open(
            account.id, credential.encrypted_password, credential.encryption_version
        )
        del password
    except ReconnectRequired:
        errors.append(
            "Save an encrypted credential for automatic reconnection before beta activation."
        )
    rules = list(
        db.scalars(
            select(BookingRule).where(
                BookingRule.campushallen_account_id == account.id, BookingRule.enabled.is_(True)
            )
        )
    )
    if not rules:
        errors.append("Create and enable at least one booking rule before activation.")
    # This is the exact worker selector, with no booking/cancellation call.
    plans = engine.discover(account.id) if not errors else []
    for rule in rules:
        matches = [p for p in plans if p.rule_id == rule.id]
        if not matches or any(
            p.outcome not in (Outcome.DRY_RUN, Outcome.ALREADY_BOOKED)
            or not p.slot
            or not p.slot.bookable_earliest
            for p in matches
        ):
            errors.append(
                "Every enabled rule needs unambiguous fresh matches and API opening times."
            )
            break
    for plan in plans:
        if not plan.slot or not db.get(BookingClaim, (account.id, plan.slot.id)):
            continue
        verified = db.scalar(
            select(BookingAttempt.id)
            .where(
                BookingAttempt.campushallen_account_id == account.id,
                BookingAttempt.activity_id == plan.slot.id,
                BookingAttempt.is_preview.is_(False),
                BookingAttempt.phase.in_(
                    ("VERIFIED_BOOKED", "VERIFIED_WAITLISTED", "VERIFIED_ALREADY_BOOKED")
                ),
            )
            .limit(1)
        )
        if not verified:
            errors.append(
                "An unresolved live claim needs operator reconciliation before activation."
            )
    return rules, plans, list(dict.fromkeys(errors))
