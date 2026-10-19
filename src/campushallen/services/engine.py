"""Database adapter around the proven group-activity selectors and booking checks.

No web dependency; external operations are injectable through AccountGateway.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from campushallen.api import GroupActivitySlot, select_group_activity_for_date
from campushallen.booking import _booked_group_activity_ids, _verified_booking_for_activity
from campushallen.database.models import (
    BookingAttempt,
    BookingClaim,
    BookingOpportunity,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
    utcnow,
)
from campushallen.schedule import BookingState

from .accounts import AccountGateway
from .notifications import (
    NoopNotifier,
    Notification,
    NotificationKind,
    NotificationService,
    deliver,
)
from .operations import snapshot
from .rules import domain_rule
from .secrets import ReconnectRequired


@dataclass(frozen=True)
class Plan:
    account_id: int
    rule_id: int
    target_date: date
    outcome: Outcome
    message: str
    slot: GroupActivitySlot | None = None


class BookingEngine:
    def __init__(
        self,
        sessions: sessionmaker,
        gateway: AccountGateway,
        notifier: NotificationService | None = None,
    ):
        self.sessions, self.gateway = sessions, gateway
        self.notifier = notifier or NoopNotifier()
        self.timezone = ZoneInfo(gateway.settings.campushallen_timezone)

    def discover(self, account_id: int, *, now: datetime | None = None) -> list[Plan]:
        now = (now or utcnow()).astimezone(self.timezone)
        plans: list[Plan] = []
        with self.sessions() as db:
            account = db.get(CampushallenAccount, account_id)
            if not account or not account.enabled or not db.get(User, account.user_id).is_active:
                return []
            rules = db.scalars(
                select(BookingRule).where(
                    BookingRule.campushallen_account_id == account_id, BookingRule.enabled.is_(True)
                )
            ).all()
            for rule in rules:
                if rule.booking_type != "group_activity":
                    plans.append(
                        Plan(
                            account_id,
                            rule.id,
                            now.date(),
                            Outcome.UNSUPPORTED,
                            "Service rules remain available through the legacy CLI; web worker does not book services.",
                        )
                    )
                    continue
                try:
                    with self.gateway.client(account) as (client, customer_id):
                        # Independent bookings GET also lets the UI show already-booked state.
                        booked = _booked_group_activity_ids(
                            client.customer_group_activity_bookings(customer_id)
                        )
                        found = False
                        discovered = []
                        for offset in range(22):
                            day = now.date() + timedelta(days=offset)
                            if day.weekday() != rule.weekday:
                                continue
                            selection = select_group_activity_for_date(
                                client, domain_rule(rule), day, self.timezone
                            )
                            slot = selection.slot
                            if slot and slot.starts_at <= now:
                                continue
                            if (
                                slot
                                and rule.start_time
                                and slot.starts_at.time() != rule.start_time
                            ):
                                slot = None
                            if slot and slot.bookable_earliest:
                                discovered.append(slot)
                                found = True
                                plans.append(
                                    Plan(
                                        account_id,
                                        rule.id,
                                        day,
                                        Outcome.DRY_RUN,
                                        "Matching activity discovered.",
                                        slot,
                                    )
                                )
                            elif selection.state in (
                                BookingState.AMBIGUOUS_TARGET,
                                BookingState.SUSPICIOUS_TARGET,
                            ):
                                found = True
                                plans.append(
                                    Plan(
                                        account_id,
                                        rule.id,
                                        day,
                                        Outcome(selection.state.value.upper()),
                                        "Selection is ambiguous or outside the allowed time window.",
                                    )
                                )
                        # Persist only after network reads finish, keeping SQLite writes brief.
                        for discovered_slot in discovered:
                            snapshot(
                                db,
                                rule,
                                discovered_slot,
                                already_booked=discovered_slot.id in booked,
                            )
                        account.last_discovery_at = utcnow()
                        account.last_error_code = None
                        db.commit()
                        if not found:
                            plans.append(
                                Plan(
                                    account_id,
                                    rule.id,
                                    now.date(),
                                    Outcome.TARGET_NOT_FOUND,
                                    "No safe upcoming target with API bookableEarliest was found.",
                                )
                            )
                except ReconnectRequired:
                    if not getattr(self.gateway, "auth_service", None):
                        account.auth_status = "reconnect_required"
                        db.commit()
                    else:
                        db.rollback()
                    plans.append(
                        Plan(
                            account_id,
                            rule.id,
                            now.date(),
                            Outcome.AUTH_FAILED,
                            "Reconnect this Campushallen account.",
                        )
                    )
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    plans.append(
                        Plan(
                            account_id,
                            rule.id,
                            now.date(),
                            Outcome.BOOKING_FAILED,
                            "Activity discovery failed; try again later.",
                        )
                    )
        return plans

    def record(
        self, plan: Plan, result: Outcome | None = None, message: str | None = None
    ) -> BookingAttempt:
        with self.sessions() as db:
            slot = plan.slot
            attempt = BookingAttempt(
                campushallen_account_id=plan.account_id,
                booking_rule_id=plan.rule_id,
                activity_id=slot.id if slot else None,
                activity_name=slot.name if slot else None,
                activity_start=slot.starts_at if slot else None,
                bookable_earliest=slot.bookable_earliest if slot else None,
                result=(result or plan.outcome).value,
                message=message or plan.message,
            )
            account = db.get(CampushallenAccount, plan.account_id)
            if not account:
                raise StalePlan("Account was removed")
            current_rule = db.get(BookingRule, plan.rule_id)
            if current_rule is None or current_rule.campushallen_account_id != plan.account_id:
                attempt.booking_rule_id = None
            db.add(attempt)
            if attempt.result in (Outcome.BOOKED, Outcome.ALREADY_BOOKED, Outcome.DRY_RUN):
                account.last_success_at = utcnow()
                account.last_error_code = None
            else:
                account.last_failure_at = utcnow()
                account.last_error_code = attempt.result
            if slot:
                opportunity = db.scalar(
                    select(BookingOpportunity).where(
                        BookingOpportunity.campushallen_account_id == plan.account_id,
                        BookingOpportunity.booking_rule_id == plan.rule_id,
                        BookingOpportunity.activity_id == slot.id,
                    )
                )
                if opportunity:
                    opportunity.worker_status = attempt.result.lower()
                    if attempt.result in (Outcome.BOOKED, Outcome.ALREADY_BOOKED):
                        opportunity.already_booked = True
                    opportunity.next_action_at = None
            db.commit()
            kind = (
                NotificationKind.BOOKING_CONFIRMED
                if attempt.result == Outcome.BOOKED
                else (
                    NotificationKind.BOOKING_FAILED
                    if attempt.result in (Outcome.BOOKING_FAILED, Outcome.VERIFICATION_FAILED)
                    else None
                )
            )
            if kind:
                deliver(self.notifier, Notification(kind, account.user_id, account.id, attempt.id))
            return attempt

    def preview_account(self, account_id: int) -> list[BookingAttempt]:
        return [self.record(plan) for plan in self.discover(account_id)]

    def run(
        self, plan: Plan, *, dry_run: bool = True, now: datetime | None = None
    ) -> BookingAttempt:
        """Re-select and check duplicates immediately before any possible mutation.

        A durable claim is committed before issuing a live request. Unknown results
        are never automatically retried. No cancellation path exists here.
        """
        now = now or utcnow()
        result, message = Outcome.BOOKING_FAILED, "Booking could not be completed."
        request_started = False
        with self.sessions() as db:
            account = db.get(CampushallenAccount, plan.account_id)
            rule = db.get(BookingRule, plan.rule_id)
            if not account or not rule or rule.campushallen_account_id != plan.account_id:
                # A queued job may outlive a deleted/reassigned rule. Do not persist stale FKs.
                raise StalePlan("Account or rule changed while queued")
            if (
                not account.enabled
                or not rule.enabled
                or not db.get(User, account.user_id).is_active
            ):
                return self.record(plan, Outcome.DISABLED, "Account, user, or rule is disabled.")
            if rule.booking_type != "group_activity":
                return self.record(
                    plan,
                    Outcome.UNSUPPORTED,
                    "Service booking is available only through the legacy CLI.",
                )
            try:
                with self.gateway.client(account) as (client, customer_id):
                    selection = select_group_activity_for_date(
                        client, domain_rule(rule), plan.target_date, self.timezone
                    )
                    slot = selection.slot
                    if not slot:
                        outcome = Outcome(selection.state.value.upper())
                        return self.record(plan, outcome, "No unique safe target was selected.")
                    if plan.slot and (
                        slot.id != plan.slot.id or slot.starts_at != plan.slot.starts_at
                    ):
                        return self.record(
                            plan,
                            Outcome.SUSPICIOUS_TARGET,
                            "Queued target changed; no booking sent.",
                        )
                    if rule.start_time and slot.starts_at.time() != rule.start_time:
                        return self.record(
                            plan, Outcome.SUSPICIOUS_TARGET, "Fixed start time no longer matches."
                        )
                    plan = Plan(
                        account.id,
                        rule.id,
                        plan.target_date,
                        Outcome.DRY_RUN,
                        "Dry-run; no booking sent.",
                        slot,
                    )
                    if slot.starts_at <= now:
                        return self.record(
                            plan, Outcome.TARGET_NOT_FOUND, "Activity has already started."
                        )
                    if not slot.bookable_earliest:
                        return self.record(
                            plan,
                            Outcome.NOT_OPEN,
                            "API did not supply bookableEarliest; no booking sent.",
                        )
                    bookings = client.customer_group_activity_bookings(customer_id)
                    if slot.id in _booked_group_activity_ids(bookings):
                        return self.record(
                            plan, Outcome.ALREADY_BOOKED, "Activity is already booked."
                        )
                    if slot.left_to_book <= 0 and not (
                        slot.has_waiting_list and rule.allow_waitlist
                    ):
                        outcome = (
                            Outcome.SKIPPED_WAITLIST_ONLY
                            if slot.has_waiting_list
                            else Outcome.BOOKING_FAILED
                        )
                        return self.record(
                            plan, outcome, "No regular spots available; no booking sent."
                        )
                    if dry_run:
                        return self.record(plan)
                    if now < slot.bookable_earliest:
                        return self.record(
                            plan, Outcome.NOT_OPEN, "API booking opening time has not arrived."
                        )
                    # End the read transaction before attempting to acquire the write claim.
                    db.rollback()
                    db.add(
                        BookingClaim(campushallen_account_id=plan.account_id, activity_id=slot.id)
                    )
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        return self.record(
                            plan,
                            Outcome.CLAIMED,
                            "A previous live attempt exists; inspect booking history before any manual retry.",
                        )
                    self.record(
                        plan,
                        Outcome.CLAIMED,
                        "Live request reserved; an interrupted request requires manual verification.",
                    )
                    request_started = True
                    client.book_group_activity(
                        customer_id, slot.id, allow_waiting_list=rule.allow_waitlist
                    )
                    # An independent GET is mandatory, regardless of POST response.
                    verified = _verified_booking_for_activity(
                        client.customer_group_activity_bookings(customer_id), slot.id
                    )
                    result = Outcome.BOOKED if verified else Outcome.VERIFICATION_FAILED
                    message = (
                        "Booking independently verified."
                        if verified
                        else "Request sent but booking could not be verified. Check Campushallen before retrying."
                    )
            except ReconnectRequired:
                if not getattr(self.gateway, "auth_service", None):
                    account.auth_status = "reconnect_required"
                    db.commit()
                else:
                    db.rollback()
                result, message = Outcome.AUTH_FAILED, "Reconnect this Campushallen account."
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                result = Outcome.VERIFICATION_FAILED if request_started else Outcome.BOOKING_FAILED
                message = (
                    "Request outcome is uncertain; check Campushallen before retrying."
                    if request_started
                    else "Campushallen request failed; no booking confirmed."
                )
        return self.record(plan, result, message)


class StalePlan(Exception):
    pass
