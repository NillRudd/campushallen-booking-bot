import argparse
import asyncio
import logging
import signal

from campushallen.database.session import make_engine, session_factory
from campushallen.services.accounts import AccountGateway
from campushallen.services.engine import BookingEngine
from campushallen.services.logging import WorkerLogFormatter
from campushallen.services.scheduler import Scheduler
from campushallen.services.secrets import TokenVault, runtime_key
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
    args = parser.parse_args()
    if args.live and not args.confirm_real_bookings:
        parser.error("Real mutations require both --live and --confirm-real-bookings")
    settings = Settings()
    handler = logging.StreamHandler()
    handler.setFormatter(WorkerLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # HTTPX logging can expose third-party URL/query data; never enable debug here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    database = make_engine(settings.database_url)
    gateway = AccountGateway(settings, TokenVault(runtime_key(settings, "encryption_key")))
    scheduler = Scheduler(
        BookingEngine(session_factory(database), gateway),
        dry_run=not args.live,
        concurrency=settings.worker_concurrency,
    )
    try:
        asyncio.run(run_worker(scheduler, once=args.once, interval=settings.worker_poll_seconds))
    except KeyboardInterrupt:
        pass
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
