from pathlib import Path

import pytest
from sqlalchemy import select

from campushallen.cli import import_config
from campushallen.database.models import BookingRule, CampushallenAccount, User
from campushallen.database.session import session_factory
from campushallen.services.authentication import create_user


def test_yaml_import_is_idempotent_owned_and_preserves_file(database):
    path = Path("config.example.yaml")
    before = path.read_bytes()
    with session_factory(database)() as db:
        user = create_user(db, "importer", "long-enough-password")
        other = User(username="other", password_hash="unused")
        db.add(other)
        db.commit()
        account = CampushallenAccount(user_id=user.id, display_name="Import", username="external")
        db.add(account)
        db.commit()
        assert import_config(db, str(path), user.id, account.id) == 1
        assert import_config(db, str(path), user.id, account.id) == 0
        rule = db.scalar(select(BookingRule))
        assert rule.product_name == "Product name shown by Campushallen"
        assert rule.weekday == 1
        assert rule.preferred_start_time.strftime("%H:%M") == "18:45"
        assert not rule.allow_waitlist
        with pytest.raises(ValueError):
            import_config(db, str(path), other.id, account.id)
    assert path.read_bytes() == before
