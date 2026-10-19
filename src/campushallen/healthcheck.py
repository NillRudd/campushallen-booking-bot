"""Container-only probes; expose neither account identifiers nor secrets."""

import sys

from campushallen.database.session import make_engine, session_factory
from campushallen.services.operations import worker_health
from campushallen.settings import Settings


def main() -> None:
    if sys.argv[1:] != ["worker"]:
        raise SystemExit(2)
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as db:
            status, state = worker_health(db, settings.worker_poll_seconds)
            if status != "Worker healthy" or state is None or not state.dry_run:
                raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
