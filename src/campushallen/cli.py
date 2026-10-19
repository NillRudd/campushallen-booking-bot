"""Local administration; deliberately no public web registration endpoint."""

import argparse
import getpass
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from campushallen.config import load_config
from campushallen.database.models import BookingRule, CampushallenAccount, User
from campushallen.database.session import make_engine, session_factory
from campushallen.schedule import BookingRule as DomainRule
from campushallen.services.authentication import create_user
from campushallen.services.rules import RuleInput
from campushallen.services.secrets import runtime_key
from campushallen.settings import Settings


def import_config(db, config_path: str, user_id: int, account_id: int) -> int:
    """Import rules atomically; never copy legacy credentials or token files."""
    account = db.scalar(
        select(CampushallenAccount).where(
            CampushallenAccount.id == account_id, CampushallenAccount.user_id == user_id
        )
    )
    if not account:
        raise ValueError("Account does not belong to this user")
    config = load_config(config_path)
    imported = 0
    for index, raw in enumerate(config.bookings, 1):
        domain = DomainRule.from_config(raw)
        data = asdict(domain)
        data["name"] = domain.name or f"{domain.activity} {index}"
        data["earliest_allowed_time"] = data.pop("allowed_start_earliest")
        data["latest_allowed_time"] = data.pop("allowed_start_latest")
        data["allow_waitlist"] = bool(raw.get("allow_waitlist", config.booking.allow_waitlist))
        if db.scalar(
            select(BookingRule.id).where(
                BookingRule.campushallen_account_id == account_id, BookingRule.name == data["name"]
            )
        ):
            continue
        validated = RuleInput.model_validate(data)
        db.add(BookingRule(campushallen_account_id=account_id, **validated.model_dump()))
        imported += 1
    db.commit()
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description="Campushallen web application administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-dev", help="Create private local development keys if needed")
    create = commands.add_parser(
        "create-user", help="Create an application user; password is prompted"
    )
    create.add_argument("username")
    importer = commands.add_parser(
        "import-config", help="Import YAML rules without moving credentials"
    )
    importer.add_argument("config_path")
    importer.add_argument("--user", required=True)
    importer.add_argument("--account-id", type=int, required=True)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "init-dev":
        runtime_key(settings, "secret_key")
        runtime_key(settings, "encryption_key")
        print("Development keys ready (values are never printed).")
        return
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as db:
            if args.command == "create-user":
                password = getpass.getpass("Application password (at least 12 characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("Passwords do not match")
                user = create_user(db, args.username, password)
                print(f"Created application user {user.username}.")
            else:
                user = db.scalar(select(User).where(User.username == args.user.strip().casefold()))
                if not user:
                    parser.error("Application user not found")
                count = import_config(db, args.config_path, user.id, args.account_id)
                print(
                    f"Imported {count} booking rules. Existing names were skipped; YAML was not changed."
                )
    except IntegrityError:
        parser.error("That username or rule name already exists")
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
