import argparse
import asyncio
import json
import logging
import signal

from campushallen.database.models import BookingReconciliation, Outcome
from campushallen.database.session import make_engine, session_factory
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway
from campushallen.services.engine import BookingEngine
from campushallen.services.logging import WorkerLogFormatter
from campushallen.services.scheduler import Scheduler
from campushallen.services.secrets import CredentialVault, TokenVault, runtime_key
from campushallen.settings import Settings


async def run_worker(scheduler: Scheduler, *, once: bool, interval: float) -> None:
    """SIGTERM cancels sleepers and drains active synchronous HTTP operations."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(scheduler.once() if once else scheduler.serve(interval))
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-account worker (dry-run by default)")
    parser.add_argument(
        "--once", action="store_true", help="Discover and process once without waiting"
    )
    parser.add_argument(
        "--live", action="store_true", help="Send real bookings; requires a second explicit opt-in"
    )
    parser.add_argument("--confirm-real-bookings", action="store_true")
    parser.add_argument(
        "--manual-approval-id",
        type=int,
        help="Consume one operator approval; requires --once and both live flags",
    )
    args = parser.parse_args()
    if args.manual_approval_id is not None and not (
        args.once and args.live and args.confirm_real_bookings
    ):
        parser.error("Manual approval requires --once --live --confirm-real-bookings")
    if args.live and not args.confirm_real_bookings:
        parser.error("Real mutations require both --live and --confirm-real-bookings")
    settings = Settings()
    handler = logging.StreamHandler()
    handler.setFormatter(WorkerLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # HTTPX logging can expose third-party URL/query data; never enable debug here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = make_engine(settings.database_url)
    master = runtime_key(settings, "encryption_key")
    tokens = TokenVault(master)
    gateway = AccountGateway(settings, tokens)
    gateway.auth_service = AccountAuthentication(
        session_factory(database), gateway, tokens, CredentialVault(master)
    )
    scheduler = Scheduler(
        BookingEngine(session_factory(database), gateway),
        dry_run=not args.live,
        concurrency=settings.worker_concurrency,
    )
    try:
        if args.manual_approval_id is not None:
            with session_factory(database)() as db:
                approval = db.get(BookingReconciliation, args.manual_approval_id)
                if not approval:
                    parser.error("Manual approval not found")
                account_id, activity_id = approval.campushallen_account_id, approval.activity_id
            plans = [
                p
                for p in scheduler.engine.discover(account_id)
                if p.slot and p.slot.id == activity_id
            ]
            if len(plans) != 1:
                parser.error("No unique approved rule target; no booking sent")
            result = scheduler.engine.run(
                plans[0], dry_run=False, manual_approval_id=args.manual_approval_id
            )
            print(
                json.dumps(
                    {
                        "attempt_id": result.id,
                        "result": result.result,
                        "phase": result.phase,
                        "http_status": result.http_status,
                        "verification": result.verification_outcome,
                    }
                )
            )
            if result.result not in (Outcome.BOOKED, Outcome.ALREADY_BOOKED):
                raise SystemExit(1)
            return
        asyncio.run(run_worker(scheduler, once=args.once, interval=settings.worker_poll_seconds))
    except KeyboardInterrupt:
        pass
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
