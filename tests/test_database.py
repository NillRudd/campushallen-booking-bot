from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from campushallen.database.models import BookingRule, CampushallenAccount, User
from campushallen.database.session import make_engine


def migrate(url):
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return config


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    migrate(url)
    engine = make_engine(url)
    yield engine
    engine.dispose()


def test_migration_round_trip_and_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    config = migrate(url)
    engine = make_engine(url)
    assert {"users", "booking_rules", "booking_attempts", "web_sessions", "booking_claims"} <= set(
        inspect(engine).get_table_names()
    )
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    engine.dispose()


def test_constraints_and_utc(database):
    with Session(database) as db:
        user = User(username="alice", password_hash="hash")
        db.add(user)
        db.commit()
        assert db.scalar(select(User)).created_at.tzinfo == timezone.utc
        db.add(User(username="alice", password_hash="other"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        account = CampushallenAccount(user_id=user.id, display_name="A", username="external")
        db.add(account)
        db.commit()
        db.add(
            BookingRule(campushallen_account_id=account.id, name="bad", activity="test", weekday=7)
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(CampushallenAccount(user_id=9999, display_name="orphan", username="x"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        user.created_at = datetime(2026, 1, 1)
        with pytest.raises(StatementError):
            db.commit()


def test_rule_deletion_preserves_attempt_history(database):
    from campushallen.database.models import BookingAttempt

    with Session(database) as db:
        user = User(username="history", password_hash="unused")
        db.add(user)
        db.commit()
        account = CampushallenAccount(
            user_id=user.id, username="history-external", display_name="History"
        )
        db.add(account)
        db.commit()
        rule = BookingRule(
            campushallen_account_id=account.id, name="History", activity="Ball", weekday=1
        )
        db.add(rule)
        db.commit()
        attempt = BookingAttempt(
            campushallen_account_id=account.id,
            booking_rule_id=rule.id,
            activity_name="Ball",
            result="DRY_RUN",
            message="Preview",
        )
        db.add(attempt)
        db.commit()
        attempt_id = attempt.id
        db.delete(rule)
        db.commit()
        db.expire_all()
        assert db.get(BookingAttempt, attempt_id).booking_rule_id is None
        assert db.get(BookingAttempt, attempt_id).activity_name == "Ball"
