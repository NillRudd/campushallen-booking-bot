"""Tenant data and durable, conservative booking claims."""

from datetime import datetime, time, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores naive UTC; callers always receive aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value else None


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class CampushallenAccount(Timestamps, Base):
    __tablename__ = "campushallen_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "username"),
        Index("uq_campushallen_account_user", "user_id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(254))
    auth_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_status: Mapped[str] = mapped_column(String(32), default="disconnected")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    live_booking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    live_booking_claim_limit: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    live_booking_activity_id: Mapped[int | None] = mapped_column(Integer)
    # Explicit ongoing admin approval; controlled grants retain their finite allowance.
    live_booking_ongoing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    requires_reconnect: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_auth_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_auth_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    auth_error_code: Mapped[str | None] = mapped_column(String(40))
    auth_failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    auth_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    auth_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_discovery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(40))


class BookingRule(Timestamps, Base):
    __tablename__ = "booking_rules"
    __table_args__ = (
        UniqueConstraint("campushallen_account_id", "name"),
        Index("uq_rule_selection", "campushallen_account_id", "selection_key", unique=True),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="valid_weekday"),
        CheckConstraint("web_category_id > 0", name="valid_category"),
        CheckConstraint("booking_type IN ('group_activity', 'service')", name="valid_booking_type"),
        CheckConstraint("earliest_allowed_time <= latest_allowed_time", name="valid_window"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), index=True
    )
    selection_key: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    activity: Mapped[str] = mapped_column(String(200))
    product_name: Mapped[str | None] = mapped_column(String(200))
    weekday: Mapped[int] = mapped_column(Integer)
    booking_type: Mapped[str] = mapped_column(String(32), default="group_activity")
    web_category_id: Mapped[int] = mapped_column(Integer, default=40)
    facility_contains: Mapped[list[str]] = mapped_column(JSON, default=list)
    product_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    business_unit_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    product_name_contains: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_time: Mapped[time | None] = mapped_column(Time)
    preferred_start_time: Mapped[time | None] = mapped_column(Time)
    earliest_allowed_time: Mapped[time] = mapped_column(Time, default=time(17))
    latest_allowed_time: Mapped[time] = mapped_column(Time, default=time(21))
    allow_waitlist: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Outcome(StrEnum):
    BOOKED = "BOOKED"
    ALREADY_BOOKED = "ALREADY_BOOKED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    SUSPICIOUS_TARGET = "SUSPICIOUS_TARGET"
    SKIPPED_WAITLIST_ONLY = "SKIPPED_WAITLIST_ONLY"
    AUTH_FAILED = "AUTH_FAILED"
    BOOKING_FAILED = "BOOKING_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    DRY_RUN = "DRY_RUN"
    NOT_OPEN = "NOT_OPEN"
    DISABLED = "DISABLED"
    CLAIMED = "CLAIMED"
    UNSUPPORTED = "UNSUPPORTED"


class BookingAttempt(Base):
    __tablename__ = "booking_attempts"
    __table_args__ = (
        Index("ix_attempt_account_created", "campushallen_account_id", "created_at"),
        CheckConstraint(
            "result IN (" + ",".join(f"'{x.value}'" for x in Outcome) + ")", name="valid_result"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE")
    )
    booking_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("booking_rules.id", ondelete="SET NULL"), index=True
    )
    activity_id: Mapped[int | None] = mapped_column(Integer)
    activity_name: Mapped[str | None] = mapped_column(String(200))
    activity_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    bookable_earliest: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Legacy attempts have unknown provenance; new records always set this explicitly.
    is_preview: Mapped[bool | None] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(40))
    phase: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int | None] = mapped_column(Integer)
    endpoint_category: Mapped[str | None] = mapped_column(String(30))
    verification_outcome: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BookingClaim(Base):
    """A live request may only be issued once per account/activity, even after a crash.

    Uncertain outcomes require manual investigation, never an automatic retry.
    """

    __tablename__ = "booking_claims"
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BookingReconciliation(Base):
    """One explicit operator-approved additional request; original claim stays immutable.

    A scheduled worker never supplies this approval ID. Consumed approvals count
    against the same account allowance forever, including uncertain outcomes.
    """

    __tablename__ = "booking_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "campushallen_account_id", "activity_id", name="uq_reconciliation_account_activity"
        ),
        Index("ix_reconciliation_account_consumed", "campushallen_account_id", "consumed_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE")
    )
    activity_id: Mapped[int] = mapped_column(Integer)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    original_claim_created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CampushallenCredential(Timestamps, Base):
    __tablename__ = "campushallen_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), unique=True
    )
    encrypted_password: Mapped[str] = mapped_column(Text)
    encryption_version: Mapped[int] = mapped_column(Integer, default=1)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    used_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class WorkerState(Base):
    __tablename__ = "worker_states"
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[str] = mapped_column(String(32))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)


class BookingOpportunity(Base):
    """Latest non-sensitive discovery snapshot; IDs identify this occurrence only."""

    __tablename__ = "booking_opportunities"
    __table_args__ = (
        UniqueConstraint("campushallen_account_id", "booking_rule_id", "activity_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), index=True
    )
    booking_rule_id: Mapped[int] = mapped_column(ForeignKey("booking_rules.id", ondelete="CASCADE"))
    activity_id: Mapped[int] = mapped_column(Integer)
    activity_name: Mapped[str] = mapped_column(String(200))
    product_name: Mapped[str] = mapped_column(String(200))
    facility: Mapped[str] = mapped_column(String(500))
    activity_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    bookable_earliest: Mapped[datetime] = mapped_column(UTCDateTime())
    left_to_book: Mapped[int] = mapped_column(Integer)
    has_waiting_list: Mapped[bool] = mapped_column(Boolean)
    already_booked: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    next_action_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    worker_status: Mapped[str] = mapped_column(String(32), default="discovered")


class WebsiteLoginBucket(Base):
    """HMAC-keyed account/IP limits, including nonexistent usernames; no raw IP/name."""

    __tablename__ = "website_login_buckets"
    __table_args__ = (CheckConstraint("attempts >= 0", name="nonnegative_login_attempts"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class BookingIdentity(Base):
    """Pseudonymous identity ownership; legacy rows survive website deletion."""

    __tablename__ = "booking_identities"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("campushallen_accounts.id", ondelete="SET NULL"), unique=True
    )
    external_label: Mapped[str | None] = mapped_column(String(64))
    external_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
