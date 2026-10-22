"""Database adapter around the proven group-activity selectors and booking checks.

No web dependency; external operations are injectable through AccountGateway.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from campushallen.api import (
    CampushallenHttpClient,
    GroupActivitySlot,
    select_group_activity_for_date,
)
from campushallen.booking import _booked_group_activity_ids, _verified_booking_for_activity
from campushallen.database.models import (
    BookingAttempt,
    BookingClaim,
    BookingOpportunity,
    BookingReconciliation,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
    utcnow,
)
from campushallen.schedule import BookingState

from .accounts import AccountGateway
from .identities import claim_identity_allowed
from .live_policy import used_claims
from .notifications import (
    NoopNotifier,
    Notification,
    NotificationKind,
    NotificationService,
    deliver,
)
from .operations import snapshot
from .provider_errors import provider_rejection
from .provider_reads import authentication_rejected
from .rules import domain_rule
from .secrets import ReconnectRequired

LOGGER = logging.getLogger(__name__)


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

    def discover(
        self, account_id: int, *, now: datetime | None = None, rule_id: int | None = None
    ) -> list[Plan]:
        now = (now or utcnow()).astimezone(self.timezone)
        plans: list[Plan] = []
        with self.sessions() as db:
            account = db.get(CampushallenAccount, account_id)
            if not account or not account.enabled or not db.get(User, account.user_id).is_active:
                return []
            rules = db.scalars(
                select(BookingRule).where(
                    BookingRule.campushallen_account_id == account_id,
                    BookingRule.enabled.is_(True),
                    *([BookingRule.id == rule_id] if rule_id is not None else []),
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
                        # A successful fresh discovery replaces observations for this rule.
                        db.execute(
                            delete(BookingOpportunity).where(
                                BookingOpportunity.booking_rule_id == rule.id,
                                BookingOpportunity.activity_id.not_in(
                                    [slot.id for slot in discovered]
                                ),
                            )
                        )
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
        self,
        plan: Plan,
        result: Outcome | None = None,
        message: str | None = None,
        *,
        update_schedule: bool = True,
        phase: str | None = None,
        http_status: int | None = None,
        endpoint_category: str | None = None,
        verification_outcome: str | None = None,
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
                is_preview=not update_schedule,
                message=message
                or (
                    "Live operation phase recorded; inspect verification outcome."
                    if phase
                    else plan.message
                ),
                phase=phase,
                http_status=http_status,
                endpoint_category=endpoint_category,
                verification_outcome=verification_outcome,
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
                if opportunity and update_schedule:
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
            if kind and update_schedule:
                deliver(self.notifier, Notification(kind, account.user_id, account.id, attempt.id))
            if update_schedule:
                LOGGER.info(
                    "Booking phase recorded",
                    extra={
                        "account_id": plan.account_id,
                        "rule_id": plan.rule_id,
                        "activity_id": slot.id if slot else None,
                        "phase": phase,
                        "http_status": http_status,
                        "result": attempt.result,
                        "verification_outcome": verification_outcome,
                    },
                )
            return attempt

    def preview_account(self, account_id: int) -> list[BookingAttempt]:
        return [self.record(plan, update_schedule=False) for plan in self.discover(account_id)]

    def preview_rule(self, account_id: int, rule_id: int) -> list[Plan]:
        plans = self.discover(account_id, rule_id=rule_id)
        for plan in plans:
            self.record(plan, update_schedule=False)
        return plans

    def run(
        self,
        plan: Plan,
        *,
        dry_run: bool = True,
        now: datetime | None = None,
        manual_approval_id: int | None = None,
    ) -> BookingAttempt:
        """Re-select and check duplicates immediately before any possible mutation.

        A durable claim is committed before issuing a live request. Unknown results
        are never automatically retried. No cancellation path exists here.
        """
        if manual_approval_id is not None and dry_run:
            raise ValueError("Manual reconciliation requires explicit live execution")
        now = now or utcnow()
        result, message = Outcome.BOOKING_FAILED, "Booking could not be completed."
        request_started = False
        phase, status, endpoint = "PREBOOK_AUTH_FAILED", None, "customer"
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
            if not dry_run and not account.live_booking_enabled:
                return self.record(
                    plan,
                    Outcome.DISABLED,
                    "Operator has not enabled live booking for this account.",
                )
            LOGGER.info(
                "Pre-book authentication",
                extra={
                    "account_id": plan.account_id,
                    "rule_id": plan.rule_id,
                    "activity_id": plan.slot.id if plan.slot else None,
                    "phase": "pre_auth",
                },
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
                            plan,
                            Outcome.ALREADY_BOOKED,
                            "Activity is already booked.",
                            phase="VERIFIED_ALREADY_BOOKED",
                            endpoint_category="bookings",
                            verification_outcome="BOOKED",
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
                    claim_result = self.claim_live(
                        plan,
                        manual_approval_id=manual_approval_id,
                        identity_digest=self.gateway.vault.identity_digest(customer_id),
                    )
                    if claim_result is not None:
                        return self.record(
                            plan,
                            claim_result,
                            "Live claim already exists or operator eligibility/attempt allowance is unavailable; no booking sent.",
                        )
                    self.record(
                        plan,
                        Outcome.CLAIMED,
                        # Counts describe the actual pre-POST read, not provider eligibility.
                        f"Authenticated bookings read: total={len(bookings)}; "
                        f"regular={sum(b.get('type') == 'groupActivityBooking' for b in bookings)}; "
                        f"waitlist={sum(b.get('type') == 'waitingListBooking' for b in bookings)}.",
                        phase="AUTH_VALIDATED",
                        http_status=200,
                        endpoint_category="bookings",
                    )
                    request_started = True
                    return self._request_and_verify(
                        plan,
                        client,
                        customer_id,
                        rule.allow_waitlist,
                        manual_approval_id=manual_approval_id,
                    )
            except ReconnectRequired as exc:
                status, endpoint = exc.http_status, exc.endpoint_category
                if not getattr(self.gateway, "auth_service", None):
                    account.auth_status = "reconnect_required"
                    db.commit()
                else:
                    db.rollback()
                result, message = Outcome.AUTH_FAILED, "Reconnect this Campushallen account."
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                result = Outcome.VERIFICATION_FAILED if request_started else Outcome.BOOKING_FAILED
                message = (
                    "Request outcome is uncertain; check Campushallen before retrying."
                    if request_started
                    else "Campushallen request failed; no booking confirmed."
                )
        return self.record(
            plan, result, message, phase=phase, http_status=status, endpoint_category=endpoint
        )

    def prepare_auth(self, plan: Plan) -> None:
        """GET-only preflight; never claims a target or marks it processed."""
        with self.sessions() as db:
            account = db.get(CampushallenAccount, plan.account_id)
            rule = db.get(BookingRule, plan.rule_id)
            if (
                not account
                or not account.enabled
                or not account.live_booking_enabled
                or not rule
                or not rule.enabled
                or rule.campushallen_account_id != account.id
                or not db.get(User, account.user_id).is_active
            ):
                return
        try:
            with self.gateway.client(account) as (client, customer):
                client.customer_group_activity_bookings(customer)
            phase = "auth_preflight_ready"
        except (ReconnectRequired, httpx.HTTPError, ValueError, KeyError, TypeError):
            phase = "auth_preflight_failed"
        LOGGER.info(
            "Pre-opening authentication check",
            extra={"account_id": plan.account_id, "rule_id": plan.rule_id, "phase": phase},
        )

    def _retry_target_allowed(
        self,
        plan: Plan,
        client: CampushallenHttpClient,
        customer: int,
        allow_waitlist: bool,
        claim_time: datetime,
    ) -> bool:
        """Refresh selectors, then recheck owner/identity/claim/intent budget under writer lock.

        Only the original stack which acquired the claim calls this. Restarts or
        competing engines fail claim_live before reaching the retry loop.
        """
        assert plan.slot is not None
        with self.sessions() as db:
            rule = db.get(BookingRule, plan.rule_id)
            if not rule or not rule.enabled or rule.allow_waitlist != allow_waitlist:
                return False
            if (
                rule.campushallen_account_id != plan.account_id
                or rule.booking_type != "group_activity"
            ):
                return False
            account = db.get(CampushallenAccount, plan.account_id)
            if not account or not account.enabled or not account.live_booking_enabled:
                return False
            selection = select_group_activity_for_date(
                client, domain_rule(rule), plan.target_date, self.timezone
            )
            slot = selection.slot
            if not slot or slot.id != plan.slot.id or slot.starts_at != plan.slot.starts_at:
                return False
            now = utcnow()
            if not slot.bookable_earliest or now < slot.bookable_earliest or now >= slot.starts_at:
                return False
            if rule.start_time and slot.starts_at.time() != rule.start_time:
                return False
            if slot.left_to_book <= 0 and not (slot.has_waiting_list and allow_waitlist):
                return False
            db.rollback()
            db.execute(
                update(CampushallenAccount)
                .where(CampushallenAccount.id == plan.account_id)
                .values(live_booking_claim_limit=CampushallenAccount.live_booking_claim_limit)
            )
            account = db.get(CampushallenAccount, plan.account_id)
            rule = db.get(BookingRule, plan.rule_id)
            claim = db.get(BookingClaim, (plan.account_id, plan.slot.id))
            posts = db.scalar(
                select(func.count())
                .select_from(BookingAttempt)
                .where(
                    BookingAttempt.campushallen_account_id == plan.account_id,
                    BookingAttempt.activity_id == plan.slot.id,
                    BookingAttempt.phase == "BOOK_POST_STARTED",
                )
            )
            allowed = bool(
                account
                and rule
                and rule.enabled
                and rule.campushallen_account_id == account.id
                and rule.allow_waitlist == allow_waitlist
                and account.enabled
                and account.live_booking_enabled
                and not account.requires_reconnect
                and db.get(User, account.user_id).is_active
                and (
                    account.live_booking_ongoing
                    or (
                        account.live_booking_claim_limit > 0
                        and used_claims(db, account.id) <= account.live_booking_claim_limit
                        and account.live_booking_activity_id in (None, plan.slot.id)
                    )
                )
                and claim
                and claim.created_at == claim_time
                and posts < 3
                and claim_identity_allowed(
                    db, account.id, self.gateway.vault.identity_digest(customer)
                )
            )
            db.rollback()
            return allowed

    def _request_and_verify(
        self,
        plan: Plan,
        client: CampushallenHttpClient,
        customer: int,
        allow_waitlist: bool,
        *,
        manual_approval_id: int | None,
    ) -> BookingAttempt:
        assert plan.slot is not None
        with self.sessions() as db:
            claim = db.get(BookingClaim, (plan.account_id, plan.slot.id))
            assert claim is not None
            claim_time = claim.created_at
        for attempt in range(1, 4):
            self.record(
                plan,
                Outcome.CLAIMED,
                f"Booking POST attempt {attempt}/3.",
                phase="BOOK_POST_STARTED",
                endpoint_category="booking",
                verification_outcome="UNKNOWN",
            )
            LOGGER.info(
                "Booking POST attempt",
                extra={
                    "account_id": plan.account_id,
                    "activity_id": plan.slot.id,
                    "phase": "booking_post_attempt",
                    "post_attempt": attempt,
                },
            )
            rejection = None
            status = None
            client.booking_http_status = None
            try:
                policy = getattr(client, "read_policy", None)
                if policy is not None:
                    policy.on_auth_rejection = lambda status: self.record(
                        plan,
                        Outcome.CLAIMED,
                        phase="VERIFY_AUTH_FAILED",
                        http_status=status,
                        endpoint_category="bookings",
                        verification_outcome="UNKNOWN",
                    )
                client.book_group_activity(
                    customer, plan.slot.id, allow_waiting_list=allow_waitlist
                )
                phase, status = "BOOK_POST_ACCEPTED", getattr(client, "booking_http_status", None)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                rejection = provider_rejection(exc.response) if 400 <= status < 500 else None
                phase = (
                    "BOOK_POST_AUTH_REJECTED"
                    if status in (401, 403)
                    and (
                        not rejection
                        or rejection.category in ("UNKNOWN_PROVIDER_REJECTION", "AUTH_REJECTED")
                    )
                    else "BOOK_POST_TIMEOUT_OR_UNCERTAIN"
                    if status >= 500 or 300 <= status < 400
                    else "BOOK_POST_REJECTED"
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                phase, status = (
                    "BOOK_POST_TIMEOUT_OR_UNCERTAIN",
                    getattr(client, "booking_http_status", None),
                )
            self.record(
                plan,
                Outcome.CLAIMED,
                rejection.message if rejection else None,
                phase=phase,
                http_status=status,
                endpoint_category="booking",
                verification_outcome="UNKNOWN",
            )
            if rejection:
                LOGGER.warning(
                    rejection.message,
                    extra={
                        "account_id": plan.account_id,
                        "activity_id": plan.slot.id,
                        "http_status": status,
                    },
                )
            try:
                # A 401 permits auth/read recovery, not a second mutation. Generic
                # 403 does not prove an expired token and causes no password login.
                if status == 401 or (rejection and rejection.category == "AUTH_REJECTED"):
                    try:
                        if self.gateway.reauthenticate(plan.account_id, client) != customer:
                            raise ReconnectRequired("Provider identity changed.")
                    except ReconnectRequired:
                        pass
                try:
                    bookings = client.customer_group_activity_bookings(customer)
                except httpx.HTTPStatusError as exc:
                    if not authentication_rejected(exc.response):
                        raise
                    self.record(
                        plan,
                        Outcome.CLAIMED,
                        phase="VERIFY_AUTH_FAILED",
                        http_status=exc.response.status_code,
                        endpoint_category="bookings",
                        verification_outcome="UNKNOWN",
                    )
                    if self.gateway.reauthenticate(plan.account_id, client) != customer:
                        raise ReconnectRequired("Provider identity changed.")
                    bookings = client.customer_group_activity_bookings(customer)
                verified = _verified_booking_for_activity(bookings, plan.slot.id)
                retry = False
                if (
                    not verified
                    and rejection
                    and rejection.code == "TOO_MANY_CONCURRENT_BOOKINGS"
                    and attempt < 3
                    and manual_approval_id is None
                ):
                    # User-confirmed maximum is four. Never guess that a generic
                    # 403/5xx/timeout was a non-mutation. Only this exact code enters.
                    for check in range(3):
                        time.sleep((0.25, 0.5, 1.0)[check])
                        bookings = client.customer_group_activity_bookings(customer)
                        verified = _verified_booking_for_activity(bookings, plan.slot.id)
                        if verified:
                            break
                        if len(bookings) < 4 and self._retry_target_allowed(
                            plan, client, customer, allow_waitlist, claim_time
                        ):
                            retry = True
                            break
                    if retry:
                        self.record(
                            plan,
                            Outcome.CLAIMED,
                            "Known booking-limit rejection; target absent and count below four; bounded retry.",
                            phase="BOOK_RETRY_READY",
                            endpoint_category="bookings",
                            http_status=200,
                            verification_outcome="NOT_BOOKED",
                        )
                        continue
                waitlisted = bool(verified and verified.get("type") == "waitingListBooking")
                return self.record(
                    plan,
                    Outcome.BOOKED if verified else Outcome.VERIFICATION_FAILED,
                    "Waiting-list entry independently verified."
                    if waitlisted
                    else "Booking independently verified."
                    if verified
                    else "Request sent but booking could not be verified. Check Campushallen before retrying.",
                    phase="VERIFIED_WAITLISTED"
                    if waitlisted
                    else "VERIFIED_BOOKED"
                    if verified
                    else "VERIFY_FAILED",
                    endpoint_category="bookings",
                    http_status=200,
                    verification_outcome="WAITLISTED"
                    if waitlisted
                    else "BOOKED"
                    if verified
                    else "NOT_BOOKED",
                )
            except (ReconnectRequired, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                http_status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else exc.http_status
                    if isinstance(exc, ReconnectRequired)
                    else None
                )
                return self.record(
                    plan,
                    Outcome.VERIFICATION_FAILED,
                    "Request outcome is uncertain; check Campushallen before retrying.",
                    phase="VERIFY_AUTH_FAILED"
                    if isinstance(exc, ReconnectRequired) or http_status in (401, 403)
                    else "VERIFY_FAILED",
                    http_status=http_status,
                    endpoint_category="bookings",
                    verification_outcome="UNKNOWN",
                )
        raise AssertionError("Booking attempt budget exhausted")

    def claim_live(
        self,
        plan: Plan,
        *,
        manual_approval_id: int | None = None,
        identity_digest: str | None = None,
    ) -> Outcome | None:
        """Serialize account eligibility, finite allowance and durable claim.

        A no-op UPDATE acquires SQLite's writer lock before counting claims.
        Uncertain claims permanently consume allowance, including across restarts.
        """
        assert plan.slot is not None
        with self.sessions() as db:
            active_rule = (
                select(BookingRule.id)
                .where(
                    BookingRule.id == plan.rule_id,
                    BookingRule.campushallen_account_id == plan.account_id,
                    BookingRule.enabled.is_(True),
                )
                .exists()
            )
            active_user = (
                select(User.id)
                .where(User.id == CampushallenAccount.user_id, User.is_active.is_(True))
                .exists()
            )
            changed = db.execute(
                update(CampushallenAccount)
                .where(
                    CampushallenAccount.id == plan.account_id,
                    CampushallenAccount.enabled.is_(True),
                    CampushallenAccount.live_booking_enabled.is_(True),
                    or_(
                        CampushallenAccount.live_booking_ongoing.is_(True),
                        CampushallenAccount.live_booking_claim_limit > 0,
                    ),
                    or_(
                        CampushallenAccount.live_booking_ongoing.is_(True),
                        CampushallenAccount.live_booking_activity_id.is_(None),
                        CampushallenAccount.live_booking_activity_id == plan.slot.id,
                    ),
                    active_rule,
                    active_user,
                )
                .values(live_booking_claim_limit=CampushallenAccount.live_booking_claim_limit)
            )
            if changed.rowcount != 1:
                db.rollback()
                return Outcome.DISABLED
            if not claim_identity_allowed(db, plan.account_id, identity_digest):
                db.rollback()
                return Outcome.DISABLED
            original_claim = db.get(BookingClaim, (plan.account_id, plan.slot.id))
            if original_claim and manual_approval_id is None:
                db.rollback()
                return Outcome.CLAIMED
            approval = None
            if manual_approval_id is not None:
                approval = db.get(BookingReconciliation, manual_approval_id)
                account = db.get(CampushallenAccount, plan.account_id)
                actor = (
                    db.get(User, approval.approved_by_user_id)
                    if approval and approval.approved_by_user_id
                    else None
                )
                if (
                    not original_claim
                    or not approval
                    or not actor
                    or not actor.is_active
                    or not actor.is_admin
                    or account.live_booking_ongoing
                    or account.live_booking_activity_id != plan.slot.id
                    or approval.campushallen_account_id != plan.account_id
                    or approval.activity_id != plan.slot.id
                    or original_claim.created_at > utcnow() - timedelta(minutes=5)
                    or approval.original_claim_created_at != original_claim.created_at
                    or approval.expires_at <= utcnow()
                ):
                    db.rollback()
                    return Outcome.DISABLED
                if approval.consumed_at is not None:
                    db.rollback()
                    return Outcome.CLAIMED
            limit = db.scalar(
                select(CampushallenAccount.live_booking_claim_limit).where(
                    CampushallenAccount.id == plan.account_id
                )
            )
            used = used_claims(db, plan.account_id)
            ongoing = db.scalar(
                select(CampushallenAccount.live_booking_ongoing).where(
                    CampushallenAccount.id == plan.account_id
                )
            )
            if not ongoing and used >= limit:
                db.rollback()
                return Outcome.DISABLED
            if approval is not None:
                approval.consumed_at = utcnow()
            else:
                db.add(
                    BookingClaim(campushallen_account_id=plan.account_id, activity_id=plan.slot.id)
                )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return Outcome.CLAIMED
            return None


class StalePlan(Exception):
    pass
