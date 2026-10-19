"""One small process: concurrent accounts, sequential mutations within an account."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from campushallen.database.models import (
    BookingAttempt,
    BookingOpportunity,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
    utcnow,
)

from .engine import BookingEngine, Plan, StalePlan
from .operations import Heartbeat

LOGGER = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        engine: BookingEngine,
        *,
        dry_run: bool = True,
        concurrency: int = 8,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.engine, self.dry_run, self.sleep = engine, dry_run, sleep
        self.semaphore = asyncio.Semaphore(concurrency)
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.account_locks: dict[int, asyncio.Lock] = {}
        self.reported: set[tuple] = set()
        self.heartbeat = Heartbeat(engine.sessions, dry_run=dry_run)

    def accounts(self) -> list[int]:
        with self.engine.sessions() as db:
            return list(
                db.scalars(
                    select(CampushallenAccount.id)
                    .join(User)
                    .where(CampushallenAccount.enabled.is_(True), User.is_active.is_(True))
                )
            )

    def still_enabled(self, plan: Plan) -> bool:
        with self.engine.sessions() as db:
            rule = db.get(BookingRule, plan.rule_id)
            account = db.get(CampushallenAccount, plan.account_id)
            return bool(
                rule
                and rule.enabled
                and account
                and account.enabled
                and rule.campushallen_account_id == account.id
                and db.get(User, account.user_id).is_active
            )

    async def discover_account(self, account_id: int) -> list[Plan]:
        try:
            async with self.semaphore:
                return await asyncio.to_thread(self.engine.discover, account_id)
        except Exception:
            # Never log third-party exceptions that could contain response bodies.
            LOGGER.error("Account discovery failed", extra={"account_id": account_id})
            return []

    def already_processed(self, plan: Plan) -> bool:
        if not plan.slot:
            return False
        with self.engine.sessions() as db:
            results = (
                [Outcome.DRY_RUN.value, Outcome.ALREADY_BOOKED.value]
                if self.dry_run
                else [
                    Outcome.BOOKED.value,
                    Outcome.ALREADY_BOOKED.value,
                    Outcome.CLAIMED.value,
                    Outcome.VERIFICATION_FAILED.value,
                ]
            )
            return (
                db.scalar(
                    select(BookingAttempt.id)
                    .where(
                        BookingAttempt.campushallen_account_id == plan.account_id,
                        BookingAttempt.booking_rule_id == plan.rule_id,
                        BookingAttempt.activity_id == plan.slot.id,
                        BookingAttempt.result.in_(results),
                    )
                    .limit(1)
                )
                is not None
            )

    def mark_processed(self, plan: Plan) -> None:
        if plan.slot is None:
            return
        with self.engine.sessions() as db:
            row = db.scalar(
                select(BookingOpportunity).where(
                    BookingOpportunity.campushallen_account_id == plan.account_id,
                    BookingOpportunity.booking_rule_id == plan.rule_id,
                    BookingOpportunity.activity_id == plan.slot.id,
                )
            )
            if row:
                latest = db.scalar(
                    select(BookingAttempt.result)
                    .where(
                        BookingAttempt.campushallen_account_id == plan.account_id,
                        BookingAttempt.booking_rule_id == plan.rule_id,
                        BookingAttempt.activity_id == plan.slot.id,
                    )
                    .order_by(BookingAttempt.id.desc())
                    .limit(1)
                )
                row.next_action_at = None
                row.worker_status = latest.lower() if latest else "completed"
                db.commit()

    async def execute(self, plan: Plan, *, wait: bool) -> None:
        try:
            if wait and plan.slot and plan.slot.bookable_earliest:
                # Sleeping does not hold a thread, DB transaction, or concurrency permit.
                # Short intervals make clock corrections and shutdown responsive.
                while (remaining := (plan.slot.bookable_earliest - utcnow()).total_seconds()) > 0:
                    await self.sleep(min(remaining, 30))
                    if not await asyncio.to_thread(self.still_enabled, plan):
                        return
            lock = self.account_locks.setdefault(plan.account_id, asyncio.Lock())
            async with lock, self.semaphore:
                operation = asyncio.create_task(
                    asyncio.to_thread(self.engine.run, plan, dry_run=self.dry_run)
                )
                try:
                    attempt = await asyncio.shield(operation)
                except asyncio.CancelledError:
                    await operation
                    raise
                LOGGER.info(
                    "Booking attempt completed",
                    extra={
                        "account_id": plan.account_id,
                        "rule_id": plan.rule_id,
                        "result": attempt.result,
                    },
                )
        except StalePlan:
            LOGGER.info("Queued rule changed or was deleted", extra={"rule_id": plan.rule_id})
        except Exception:
            LOGGER.error(
                "Booking task failed",
                extra={"account_id": plan.account_id, "rule_id": plan.rule_id},
            )

    async def refresh(self, *, wait: bool = True) -> None:
        # Forget expired jobs and discovery errors; keep memory bounded over long runs.
        today = utcnow().date()
        self.reported = {key for key in self.reported if key[-1] >= today}
        self.tasks = {
            key: task for key, task in self.tasks.items() if not task.done() or key[-1] >= today
        }
        batches = await asyncio.gather(
            *(self.discover_account(a) for a in await asyncio.to_thread(self.accounts))
        )
        for plans in batches:
            for plan in plans:
                key: tuple
                if not plan.slot:
                    key = (plan.account_id, plan.rule_id, plan.outcome, today)
                    if key not in self.reported:
                        try:
                            await asyncio.to_thread(self.engine.record, plan)
                            self.reported.add(key)
                        except (StalePlan, SQLAlchemyError):
                            LOGGER.warning(
                                "Discovery result became unavailable",
                                extra={"account_id": plan.account_id},
                            )
                    continue
                key = (
                    plan.account_id,
                    plan.rule_id,
                    plan.slot.id,
                    plan.slot.starts_at,
                    plan.slot.bookable_earliest,
                    plan.target_date,
                )
                if key in self.tasks:
                    if self.tasks[key].done():
                        await asyncio.to_thread(self.mark_processed, plan)
                    continue
                if await asyncio.to_thread(self.already_processed, plan):
                    await asyncio.to_thread(self.mark_processed, plan)
                    continue
                if (
                    not wait
                    and plan.slot.bookable_earliest
                    and plan.slot.bookable_earliest > utcnow()
                    and not self.dry_run
                ):
                    continue
                self.tasks[key] = asyncio.create_task(self.execute(plan, wait=wait))

    async def once(self) -> None:
        # Dry-run reports future matches immediately; live mode only attempts due targets.
        await asyncio.to_thread(self.heartbeat.beat)
        try:
            await self.refresh(wait=False)
            await asyncio.gather(*self.tasks.values())
        finally:
            await asyncio.to_thread(self.heartbeat.beat, stopped=True)

    async def heartbeat_loop(self, interval: float) -> None:
        while True:
            await asyncio.to_thread(self.heartbeat.beat)
            await asyncio.sleep(min(30, interval))

    async def serve(self, interval: float = 60) -> None:
        pulse = asyncio.create_task(self.heartbeat_loop(interval))
        try:
            while True:
                await self.refresh()
                await self.sleep(interval)
        finally:
            # asyncio.to_thread cannot interrupt an HTTP request already in flight.
            # Drain active attempts so their verified outcomes can be persisted.
            for task in self.tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await asyncio.to_thread(self.heartbeat.beat, stopped=True)
