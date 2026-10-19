# Campushallen Booker

A development-stage, multi-user web application built around the existing
Campushallen / Go Active booking bot. Manage accounts and booking rules, preview
upcoming activities, and inspect booking history. The original YAML-based CLI
continues to work.

**Development only. Deployment is intentionally deferred.** The Raspberry Pi,
systemd units, and installer have not been changed. No real booking is needed to
run the tests or start the application.

## Local development

Requires Python **3.12+**. Docker is optional. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # only if you do not already have a local .env
python -m campushallen.cli init-dev
alembic upgrade head
python -m campushallen.cli create-user YOUR_USERNAME
make dev
```

Open <http://127.0.0.1:8000>. The create-user command prompts for a password
(minimum 12 characters); there are no default users, default passwords, or public
registration endpoints. Run it again with another username to add another user.

After logging in:

1. Add a Campushallen account under **Accounts**.
2. Open the account and connect using its Campushallen password.
3. Create a booking rule under **Rules**.
4. Use **Preview matching activities** on the account page.
5. Inspect matches, API opening times, and results in **History** and the dashboard.

Preview is read-only against Campushallen. It saves local discovery results but
never calls a booking or cancellation endpoint. Connecting performs the normal
Campushallen authentication request. No credentials are required just to explore
the UI or run tests.

## Docker development

Requires Docker with Compose v2 (including optional `env_file` support).

```sh
docker compose up --build
```

The web container generates private development keys if absent, applies Alembic
migrations, and starts at <http://127.0.0.1:8000>. In another terminal:

```sh
docker compose exec web campushallen-admin create-user YOUR_USERNAME
```

Source and migration directories are mounted read-only; Uvicorn reloads source
changes. SQLite and generated keys live in the persistent `app-data` named
volume. The container runs as UID 10001, with a healthcheck and graceful shutdown.
The port is bound to the host loopback interface. `.env` is optional and is never
copied into the image. Local and Docker databases are separate by default.

```sh
docker compose --profile worker up --build  # web + separate dry-run worker
make docker-down                          # stop; retain data
```

The worker profile never enables real bookings. `docker compose down -v` would
remove the development database and keys; ordinary `make docker-down` keeps them.
Browser binaries are unnecessary for the HTTP API/web application. The legacy
interactive browser login still requires Playwright Chromium installed locally.

## Configuration and secrets

Settings use pydantic-settings: environment variables override `.env`.
Legacy CLI configuration remains in `config.yaml` and is independent of web
settings.

| Variable | Development default / purpose |
| --- | --- |
| `APP_ENV` | `development` |
| `DATABASE_URL` | `sqlite:///state/web.db` |
| `STATE_DIR` | `state`; generated keys live here |
| `SECRET_KEY` | Session hashing / signed CSRF tokens; at least 32 characters |
| `ENCRYPTION_KEY` | Fernet key for authenticated encryption of Campushallen tokens |
| `CAMPUSHALLEN_TIMEZONE` | `Europe/Stockholm` |
| `SECURE_COOKIES` | `false` for local HTTP; HTTPS installations must use `true` |
| `SESSION_HOURS` | `12` |
| `WORKER_POLL_SECONDS` | `60` |
| `WORKER_CONCURRENCY` | `8` concurrent account operations |

In development, absent keys are generated once into `STATE_DIR/secret_key` and
`STATE_DIR/encryption_key` with `0600` permissions. They persist across restarts.
Outside development both keys must be provided through the environment; there
is no hard-coded fallback. Generate independent values when configuring them:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Keep keys private and outside SQLite. Back up the token encryption key separately
from the database. Losing or changing it requires reconnecting accounts; changing
the session secret invalidates existing web sessions.

Application passwords are Argon2id hashes. Browser sessions use random opaque
cookies, with only an HMAC digest stored in SQLite; logout deletes the database
session. Cookies are HTTP-only and SameSite, and all mutating forms require a
signed CSRF token. Queries enforce user ownership before accessing account or
rule IDs. Templates escape user content. Login and account connection attempts
have a bounded, in-process rate limiter. Use a single web process in this phase;
a shared limiter would be needed before using multiple processes.

Campushallen passwords are used only for initial authentication/reconnection and
are never written to SQLite. Token state is encrypted, never rendered in HTML,
and never loaded from another account's CLI files. The existing API implements
login and token validation but **does not implement a proven refresh flow**.
Expired/invalid tokens produce `AUTH_FAILED` and require reconnection. Indefinite
unattended authentication is not promised; no plaintext password is retained to
work around expiry. The legacy CLI's existing local token files are unchanged.

`.env`, local YAML, databases, state, logs, tokens, and keys are ignored by Git
and excluded from the Docker build context. Do not share the entire working
directory as an archive. Never enable HTTP debug/body logging with real secrets.

## Architecture

```text
Browser → FastAPI routes → services → existing Campushallen HTTP API / selectors
                          ↕
                       SQLite
                          ↕
                 independent worker
```

- `src/campushallen/api.py`, `schedule.py`, `booking.py`: existing API,
  matching, duplicate detection, verification, and YAML CLI behavior.
- `database/`: SQLAlchemy 2 models, UTC datetime adapter, SQLite sessions.
- `migrations/`: versioned Alembic migrations, including constraints and indexes.
- `services/accounts.py`, `secrets.py`: per-account clients and encrypted token state.
- `services/engine.py`: database-backed discovery, safety checks, independent
  verification, outcomes, and durable live-attempt claims; no FastAPI dependency.
- `services/scheduler.py`, `worker.py`: concurrent accounts, independent failures,
  local waiting until API-provided opening times, sequential requests per account.
- `web/`: application factory, ownership/CSRF dependencies, small route modules,
  Jinja templates, and responsive CSS. No frontend build step.
- `cli.py`: local user creation and YAML import.

See [the audit and design notes](docs/development-design.md) for migration choices
and current limits. UTC is stored internally, including SQLite round trips;
business-day matching and displayed times use the configured Stockholm timezone.

## Worker and booking safety

The web server does not start a scheduler. Run one worker separately:

```sh
campushallen-worker --once  # discover + dry-run immediately, then exit
campushallen-worker         # discover repeatedly; wait locally for API openings
# Equivalent: python -m campushallen.worker --once
```

The worker discovers enabled group-activity rules up to 21 days ahead. It reuses
the existing product, weekday, facility, time-window, and ambiguity selectors.
The API's `bookableEarliest` is mandatory; the new engine never estimates it.
Immediately before attempting, it refreshes selection, checks target identity,
checks existing bookings, and enforces waitlist policy. Each account has its own
client and credentials. Multiple accounts can issue requests concurrently at the
same opening; failures in one account do not block the others.

**Real bookings require an explicit operator opt-in:** both `--live` and
`--confirm-real-bookings`. Neither is enabled in Compose or Make targets. Do not
use them for routine development or automated tests. No automated real booking
or cancellation has been performed during this implementation.

Live requests get a durable unique `(account, activity)` claim before the POST.
The engine verifies success with an independent bookings GET. A timeout, crash,
or failed verification leaves the claim in place: inspect Campushallen manually
before any retry. There is deliberately no automatic claim-reset or cancellation
feature. Dry-run records do not create live claims. Outcomes include `BOOKED`,
`ALREADY_BOOKED`, `TARGET_NOT_FOUND`, `AMBIGUOUS_TARGET`, `SUSPICIOUS_TARGET`,
`SKIPPED_WAITLIST_ONLY`, `AUTH_FAILED`, `BOOKING_FAILED`, `VERIFICATION_FAILED`,
`DRY_RUN`, `NOT_OPEN`, `DISABLED`, `CLAIMED`, and `UNSUPPORTED`.

Service rules can be stored/imported for compatibility, but the web worker
reports `UNSUPPORTED`; existing service CLI discovery remains available. The
existing fixed-time/service CLI paths keep their existing live-test guards.
The existing dynamic CLI path can send real bookings with `--live`; use dry-run
for development. No production behavior or timer has been changed.

## Database migrations

Run from the repository root with your intended `DATABASE_URL`:

```sh
make migrate                          # alembic upgrade head
alembic current
alembic check                         # verify models agree with migrations
alembic revision --autogenerate -m "describe change"
```

Review generated migrations before applying them. No application startup path
uses `create_all()`. Docker applies migrations on web startup for development;
local startup requires the explicit migration command. `/health` returns 503
until the database has its migrated user table. Back up SQLite before schema
changes. Automated tests exercise upgrade, downgrade, constraints, and UTC storage.

## Importing existing YAML rules

Keep using existing YAML while building the database configuration. Import never
overwrites YAML, copies credentials, or touches legacy token/session files.

1. Create an application user.
2. Add its Campushallen account through the UI and note the numeric account ID
   in its URL (for example `/accounts/3`).
3. Import into that user's account:

```sh
python -m campushallen.cli import-config config.yaml --user YOUR_USERNAME --account-id 3
```

Rules are validated and imported atomically. Existing names within the account
are skipped, so the command can be rerun. YAML product IDs, business unit IDs,
name fragments, fixed/preferred times, facility filters, and global waitlist
preference are preserved. Connect the account independently through the UI.
The importer verifies that the destination account belongs to the named user.

## Existing CLI

Existing entry points and scripts remain available:

```sh
cp config.example.yaml config.yaml     # only when no local config exists
campushallen-book --dry-run
campushallen-book --dry-run --wait
campushallen-inspect-schedule
```

For interactive browser login:

```sh
python -m playwright install chromium
campushallen-login
```

For the existing `.env` credential login, configure `CAMPUSHALLEN_USERNAME` and
`CAMPUSHALLEN_PASSWORD` locally and run `campushallen-login --env`. This is the
legacy workflow; web accounts do not use those variables or shared files.
`config.example.yaml` documents the YAML fields. Keep local credentials private.

## Verification and commands

```sh
make test       # all tests; external network access is forbidden by test fixtures
make lint       # Ruff checks + formatting for new modules
make typecheck  # mypy for new web/database/service modules
make format
make migrate
make dev
make worker     # dry-run
make docker-up
make docker-down
```

Tests use fake gateways or HTTPX MockTransport and require no real credentials.
They cover migrations, constraints, auth/session invalidation, CSRF, tenant
isolation, account token storage, rule CRUD/validation, YAML import, target safety,
waitlist opt-in, duplicate protection, independent verification, concurrent
accounts, and account failure isolation. The original tests remain intact.
Legacy formatting is intentionally retained; mypy is scoped to the new code.
See [the verification record](docs/development-validation.md) for tested versions
and smoke-test results.

The Docker smoke check is `docker compose up --build --wait`, followed by
`curl -f http://127.0.0.1:8000/health` and opening `/login`. Avoid running a live
worker during smoke tests. Deployment instructions for the Raspberry Pi and
production hardening will be handled in a later phase.
