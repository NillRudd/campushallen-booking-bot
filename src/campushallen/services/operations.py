"""Durable discovery snapshots and heartbeat, independent of HTTP routes."""

import secrets
from datetime import timedelta
from importlib.metadata import version

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from campushallen.database.models import (
    BookingOpportunity,
    BookingRule,
    CampushallenAccount,
    WorkerState,
    utcnow,
)


def snapshot(db, rule, slot, *, already_booked: bool) -> None:
    from sqlalchemy.dialects.sqlite import insert

    now = utcnow()
    values = dict(
        campushallen_account_id=rule.campushallen_account_id,
        booking_rule_id=rule.id,
        activity_id=slot.id,
        activity_name=slot.name,
        product_name=slot.product_name or slot.name,
        facility=", ".join(slot.location_names) or slot.business_unit_name or "",
        activity_start=slot.starts_at,
        bookable_earliest=slot.bookable_earliest,
        left_to_book=slot.left_to_book,
        has_waiting_list=slot.has_waiting_list,
        already_booked=already_booked,
        observed_at=now,
        next_action_at=None if already_booked else max(now, slot.bookable_earliest),
        worker_status="already_booked"
        if already_booked
        else "waiting"
        if slot.bookable_earliest > now
        else "ready",
    )
    statement = insert(BookingOpportunity).values(**values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["campushallen_account_id", "booking_rule_id", "activity_id"],
            set_=values,
        )
    )


class Heartbeat:
    def __init__(self, sessions: sessionmaker, *, dry_run: bool):
        self.sessions, self.dry_run = sessions, dry_run
        self.worker_id = secrets.token_hex(16)

    def beat(self, *, stopped: bool = False) -> None:
        with self.sessions() as db:
            db.execute(
                delete(WorkerState).where(WorkerState.heartbeat_at < utcnow() - timedelta(days=7))
            )
            row = db.get(WorkerState, self.worker_id)
            if not row:
                row = WorkerState(
                    worker_id=self.worker_id,
                    version=version("campushallen-bot"),
                    dry_run=self.dry_run,
                )
                db.add(row)
            row.heartbeat_at = utcnow()
            row.stopped_at = utcnow() if stopped else None
            db.commit()


def worker_health(db, poll_seconds: float) -> tuple[str, WorkerState | None]:
    latest = db.scalar(
        select(WorkerState)
        .where(WorkerState.stopped_at.is_(None))
        .order_by(WorkerState.heartbeat_at.desc())
        .limit(1)
    )
    if latest:
        healthy = latest.heartbeat_at > utcnow() - timedelta(seconds=max(90, poll_seconds * 3))
        return ("Worker healthy" if healthy else "Worker hasn't reported recently"), latest
    latest = db.scalar(select(WorkerState).order_by(WorkerState.heartbeat_at.desc()).limit(1))
    return "Worker disabled / not running", latest


def account_cards(db, user_id: int):
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user_id)
    ).all()
    cards = []
    for account in accounts:
        next_slot = db.scalar(
            select(BookingOpportunity)
            .join(BookingRule)
            .where(
                BookingOpportunity.campushallen_account_id == account.id,
                BookingOpportunity.activity_start > utcnow(),
                BookingRule.enabled.is_(True),
            )
            .order_by(BookingOpportunity.activity_start)
            .limit(1)
        )
        cards.append((account, next_slot))
    return cards
