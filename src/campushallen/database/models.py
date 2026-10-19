"""Tenant data and durable, conservative booking claims."""
from datetime import datetime, time, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, Time, UniqueConstraint
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


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class CampushallenAccount(Timestamps, Base):
    __tablename__ = "campushallen_accounts"
    __table_args__ = (UniqueConstraint("user_id", "username"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(254))
    auth_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_status: Mapped[str] = mapped_column(String(32), default="disconnected")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BookingRule(Timestamps, Base):
    __tablename__ = "booking_rules"
    __table_args__ = (
        UniqueConstraint("campushallen_account_id", "name"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="valid_weekday"),
        CheckConstraint("web_category_id > 0", name="valid_category"),
        CheckConstraint("booking_type IN ('group_activity', 'service')", name="valid_booking_type"),
        CheckConstraint("earliest_allowed_time <= latest_allowed_time", name="valid_window"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), index=True)
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
        CheckConstraint("result IN (" + ",".join(f"'{x.value}'" for x in Outcome) + ")", name="valid_result"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campushallen_account_id: Mapped[int] = mapped_column(ForeignKey("campushallen_accounts.id", ondelete="CASCADE"))
    booking_rule_id: Mapped[int | None] = mapped_column(ForeignKey("booking_rules.id", ondelete="SET NULL"), index=True)
    activity_id: Mapped[int | None] = mapped_column(Integer)
    activity_name: Mapped[str | None] = mapped_column(String(200))
    activity_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    bookable_earliest: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    result: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BookingClaim(Base):
    """A live request may only be issued once per account/activity, even after a crash.

    Uncertain outcomes require manual investigation, never an automatic retry.
    """
    __tablename__ = "booking_claims"
    campushallen_account_id: Mapped[int] = mapped_column(ForeignKey("campushallen_accounts.id", ondelete="CASCADE"), primary_key=True)
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
