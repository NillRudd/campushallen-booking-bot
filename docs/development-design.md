# Repository audit and development design

## Existing system

The original repository is a setuptools `src` Python package. Entry points are
`campushallen-login`, `campushallen-book`, and `campushallen-inspect-schedule`,
with matching script wrappers. YAML dataclasses configure a single account,
global file paths, booking defaults, and per-rule selection filters. There were
24 passing pytest tests before this extension.

`api.py` implements synchronous HTTPX operations against the BRP API, including
login, activities, existing bookings, booking, and cancellation. It automatically
loads browser cookies and bearer tokens from configured local files. `auth.py`
provides manual Playwright login and direct API login; token state contains
access/refresh tokens, expiry, and the numeric customer identifier derived from
the login response. There is no implemented refresh endpoint or tested guarantee
that the state can authenticate indefinitely.

`schedule.py` contains domain rules and time/product matching. `api.py` contains
proven dynamic group-activity selectors which fail on ambiguity and suspicious
time windows. `booking.py` contains local waits, duplicate detection, refreshing,
POST booking, and independent verification. Dynamic group rules support actual
booking; fixed-time and service rules retain live-test guards. The old README
understated this distinction. The existing implementation sometimes falls back
to a calculated opening if API metadata is absent; the new engine fails closed
instead, without changing the existing CLI.

`systemd/` and the installer target a single CLI checkout. They are preserved
without modification; this development work does not install services or alter
the Raspberry Pi.

## Decisions and migration strategy

Retain the existing package and public CLI behavior. Add `database`, `services`,
and `web` subpackages rather than restructure proven code. The only behavioral
addition to the legacy API client is an optional `load_saved_auth=False` switch:
web accounts must never inherit the global CLI account's cookies or tokens.

A database adapter constructs the existing domain rule and uses the existing
selectors and duplicate/verification helpers. This isolates database scheduling
from the existing YAML runner while preserving the proven matching logic. Routes
handle HTTP forms and ownership; they never select or book Campushallen sessions.

Use SQLAlchemy 2 and versioned Alembic operations. All application users own
accounts; rules and attempts reference accounts. Sessions are opaque and
revocable in the database. Rule deletion preserves attempt history by setting
its rule reference to null. The UTC type rejects naive writes and restores aware
UTC datetimes after SQLite reads. SQLite uses foreign keys, WAL, and a busy
timeout. Each request/worker operation owns its session; no ORM session is shared
between threads. A compound live claim prevents concurrent/restarted workers
from repeating an uncertain request for the same account and activity.

Store only encrypted Campushallen token state, with an external Fernet key.
Discard passwords after login. Do not guess refresh semantics or retain
passwords for reauthentication; reconnect explicitly on expiry. Development keys
are random, private persistent files unless explicitly supplied by environment.
No default application account is created. Local administration creates users
through a password prompt. Central account/rule ownership queries return 404 for
foreign IDs, including all mutation and preview routes.

A separate worker runs an asyncio scheduler. Synchronous HTTPX and SQLAlchemy
operations run in threads, with distinct sessions and clients. Sleeping for an
API opening holds no thread, transaction, or concurrency permit. Account locks
serialize mutations per account; a configurable semaphore bounds concurrent
operations across accounts. Discovery failures are isolated. Targets are
reselected at execution time; moved sessions fail closed. Changed API openings
get separate schedules. The worker drains active operations on shutdown and
retains claims if interrupted during uncertain outcomes.

YAML remains valid for the original bot. Import is explicit, atomic, idempotent
by rule name within an account, ownership-checked, and copies no credentials.
The database becomes the web worker's source of truth without modifying any
existing user's local YAML or state.

## Current development limits

- No deployment or production systemd changes. Python 3.12 is now the package's
  minimum for new installs; no existing Raspberry Pi environment was upgraded.
- No proven automatic token refresh. Reconnection is required on expiry.
- Service discovery remains in the legacy CLI; the new worker books only group
  activities. It reports unsupported service rules explicitly.
- Dashboard opportunities are cached from previews/worker attempts, with their
  discovery timestamp. They are not a promise that a spot is still available.
- SQLite and a single worker/web process are intended for small installations.
  The in-process rate limiter must be replaced before scaling web processes.
- Claims deliberately favor avoiding duplicate requests over automatic recovery.
  Recovery after an uncertain request is a manual operational decision.
- The web UI exposes only read-only previews, not a live-booking action. Worker
  live mode needs two explicit operator flags and has only been exercised with
  fakes in tests.
- API sessions are refreshed and verified before a live request, so execution
  includes network latency after the opening timestamp. It is not a hard
  real-time scheduling guarantee.
- Automated tests block external sockets. Docker and local smoke checks use an
  empty development database and never authenticate to or mutate Campushallen.
