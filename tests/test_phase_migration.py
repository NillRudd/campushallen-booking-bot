from alembic import command
from alembic.config import Config
from sqlalchemy import text

from campushallen.database.session import make_engine


def test_upgrade_and_downgrade_preserve_previous_phase_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'previous.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "5cb3ccd7962c")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_active, created_at, updated_at) VALUES (1,'existing','hash',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO campushallen_accounts (id,user_id,display_name,username,auth_state,auth_status,enabled,created_at,updated_at) VALUES (1,1,'Existing','external','unchanged-ciphertext','connected',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO web_sessions (token_hash,user_id,expires_at) VALUES ('hash',1,'2099-01-01')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT is_admin FROM users")).scalar() == 0
        assert (
            db.execute(text("SELECT auth_state FROM campushallen_accounts")).scalar()
            == "unchanged-ciphertext"
        )
        assert db.execute(text("SELECT count(*) FROM campushallen_credentials")).scalar() == 0
    command.check(config)
    command.downgrade(config, "5cb3ccd7962c")
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT auth_state FROM campushallen_accounts")).scalar()
            == "unchanged-ciphertext"
        )
        assert db.execute(text("SELECT count(*) FROM web_sessions")).scalar() == 1
    command.upgrade(config, "head")
    engine.dispose()


def test_phase_upgrade_preserves_spent_incident_claim_and_auth_history(tmp_path):
    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite:///{tmp_path}/incident.db"
    command.upgrade(config, "f2c9a71e6b04")
    engine = make_engine(config.attributes["database_url"])
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,username,password_hash,is_active,is_admin,created_at,updated_at) VALUES(1,'niklas','test',1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO campushallen_accounts(id,user_id,display_name,username,auth_status,enabled,live_booking_enabled,live_booking_claim_limit,live_booking_activity_id,created_at,updated_at) VALUES(2,1,'private','private','connected',1,1,1,4343840,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO booking_claims(campushallen_account_id,activity_id,created_at) VALUES(2,4343840,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO booking_attempts(campushallen_account_id,activity_id,result,message,attempted_at,created_at,is_preview) VALUES(2,4343840,'AUTH_FAILED','Historical',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0)"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT activity_id FROM booking_claims")).fetchall() == [(4343840,)]
        assert db.execute(text("SELECT result,phase FROM booking_attempts")).fetchall() == [
            ("AUTH_FAILED", None)
        ]
        assert db.execute(
            text(
                "SELECT live_booking_ongoing,live_booking_claim_limit,live_booking_activity_id FROM campushallen_accounts"
            )
        ).fetchone() == (0, 1, 4343840)
    command.check(config)
    engine.dispose()
