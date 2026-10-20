"""Local administration; deliberately no public web registration endpoint."""

import argparse
import getpass
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from campushallen.config import load_config
from campushallen.database.models import BookingRule, CampushallenAccount, Invitation, User, utcnow
from campushallen.database.session import make_engine, session_factory
from campushallen.schedule import BookingRule as DomainRule
from campushallen.services.authentication import create_user
from campushallen.services.backups import backup_database, verify_restore
from campushallen.services.invitations import create_invite, invite_status, revoke_invite
from campushallen.services.live_policy import configure_live
from campushallen.services.rules import RuleInput
from campushallen.services.secrets import runtime_key
from campushallen.services.user_security import set_admin
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
        data["allow_waitlist"] = raw.get("allow_waitlist", config.booking.allow_waitlist)
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
    backup = commands.add_parser(
        "backup", help="Create a consistent SQLite snapshot; keys stay separate"
    )
    backup.add_argument("--directory", type=Path, required=True)
    backup.add_argument("--keep", type=int, default=14)
    restore = commands.add_parser(
        "restore-test", help="Verify a backup in a temporary isolated database"
    )
    restore.add_argument("backup", type=Path)
    restore.add_argument("--expect-user")
    live = commands.add_parser(
        "live-eligibility", help="Inspect/configure operator-only live scope"
    )
    live.add_argument("account_id", type=int)
    live.add_argument("--user", required=True)
    switch = live.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    live.add_argument("--max-claims", type=int)
    live.add_argument("--activity-id", type=int)
    create = commands.add_parser(
        "create-user", help="Create an application user; password is prompted"
    )
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    for action in ("promote", "demote"):
        commands.add_parser(action).add_argument("username")
    invite = commands.add_parser("invite", help="Create, list, or revoke one-time invitations")
    invite_commands = invite.add_subparsers(dest="invite_action", required=True)
    invite_create = invite_commands.add_parser("create")
    invite_create.add_argument("--label", default="")
    invite_create.add_argument("--count", type=int, default=1)
    invite_create.add_argument("--expires-days", type=int, default=7, help="0 means no expiry")
    invite_commands.add_parser("list")
    invite_commands.add_parser("revoke").add_argument("id", type=int)
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
    if args.command in ("backup", "restore-test"):
        try:
            if args.command == "backup":
                print(backup_database(settings.database_url, args.directory, keep=args.keep))
            else:
                verify_restore(
                    args.backup,
                    runtime_key(settings, "encryption_key"),
                    expected_user=args.expect_user,
                )
                print(
                    "Isolated restore and synthetic encryption checks passed; production was not modified."
                )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as db:
            if args.command == "create-user":
                password = getpass.getpass("Application password (at least 12 characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("Passwords do not match")
                user = create_user(db, args.username, password, is_admin=args.admin)
                print(f"Created application user {user.username}.")
            elif args.command in ("promote", "demote"):
                user = db.scalar(
                    select(User).where(User.username == args.username.strip().casefold())
                )
                if not user:
                    parser.error("Application user not found")
                set_admin(db, user, enabled=args.command == "promote")
                print("Admin status updated.")
            elif args.command == "live-eligibility":
                import json

                print(
                    json.dumps(
                        configure_live(
                            db,
                            args.account_id,
                            args.user,
                            enabled=True if args.enable else False if args.disable else None,
                            limit=args.max_claims,
                            activity_id=args.activity_id,
                        )
                    )
                )
            elif args.command == "invite":
                if args.invite_action == "create":
                    if not 1 <= args.count <= 100 or not 0 <= args.expires_days <= 365:
                        parser.error("Use count 1–100 and expiry days 0–365")
                    links = []
                    for _ in range(args.count):
                        row, token = create_invite(
                            db,
                            label=args.label,
                            expires_at=utcnow() + timedelta(days=args.expires_days)
                            if args.expires_days
                            else None,
                        )
                        links.append(f"{settings.public_base_url}/invite/{token}")
                    db.commit()
                    print("Save these invitation links now. They cannot be displayed again.")
                    for link in links:
                        print(link)
                elif args.invite_action == "list":
                    for row in db.scalars(select(Invitation).order_by(Invitation.id)):
                        print(
                            f"{row.id}  {row.label!r}  {invite_status(row)}  expires={row.expires_at or 'never'}"
                        )
                else:
                    print(
                        "Invitation revoked."
                        if revoke_invite(db, args.id)
                        else "No unused invitation changed."
                    )
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
