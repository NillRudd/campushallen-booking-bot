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
