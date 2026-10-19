# Campushallen Booker

A development-stage, multi-user web application built around the existing
Campushallen / Go Active booking bot. Join by invitation, manage private accounts
and booking rules, preview upcoming activities, and inspect booking history.
Optional encrypted credentials allow automatic reconnection; a separate worker
reports its health and planned actions. The original YAML-based CLI
continues to work.

Production deployment support is prepared in [the Raspberry Pi runbook](docs/production-deployment.md).
That runbook records the actual deployment status; preparation alone does not
mean the public site is running. The new worker remains **dry-run**, and the
legacy live timers are reserved for a later, explicitly approved cutover.
No real booking is needed to run tests or start the application.

## Local development

Requires Python **3.12+**. Docker is optional. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # only if you do not already have a local .env
python -m campushallen.cli init-dev
alembic upgrade head
python -m campushallen.cli create-user YOUR_USERNAME --admin
make dev
```

Open <http://127.0.0.1:8000>. The create-user command prompts for a password
(minimum 12 characters); there are no default users, default passwords, or open registration endpoints.
The operator can create users locally or issue single-use invitation links.

After logging in:

1. Add a Campushallen account under **Accounts**.
2. Open the account and connect using its Campushallen password. Choose whether
   to remember it for automatic reconnection; storage is off for a new account.
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
docker compose exec web campushallen-admin create-user YOUR_USERNAME --admin
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
Playwright and browser binaries are unnecessary for the HTTP API/web application.
Playwright is included in the development extra and the optional `browser` extra. The legacy
interactive browser login still requires Playwright Chromium installed locally.

## Configuration and secrets

Settings use pydantic-settings: environment variables override `.env`.
Legacy CLI configuration remains in `config.yaml` and is independent of web
settings.

| Variable | Development default / purpose |
| --- | --- |
| `APP_ENV` | `development` |
| `DEFAULT_LOCALE` | `sv_SE`; currently the only supported web locale |
| `DATABASE_URL` | `sqlite:///state/web.db` |
| `STATE_DIR` | `state`; generated keys live here |
| `SECRET_KEY` | Session hashing / signed CSRF tokens; at least 32 characters |
| `ENCRYPTION_KEY` | Master Fernet-format key; purpose-separated token/credential encryption |
| `SECRET_KEY_FILE`, `ENCRYPTION_KEY_FILE` | Optional mounted secret-file paths; explicit key values take precedence |
| `ALLOWED_HOSTS` | JSON array; production requires the single public hostname |
| `TRUSTED_TUNNEL_IPS` | JSON array of exact trusted connector peer IPs; empty in development |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000`; origin for generated invite links |
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
rule IDs. Templates escape user content. Website login has persistent account and IP limits: ten failures within 30 minutes
lock the submitted name for 30 minutes; source IPs allow twenty submissions per
15 minutes. Unknown names follow the same policy. Other sensitive actions retain
a bounded in-process limiter. Use one web process; see the
[operational policy](docs/production-operations.md) before changing this topology.

Campushallen passwords are discarded after connection unless you explicitly opt
in per account. Remembered passwords live only as authenticated ciphertext in a
dedicated credential table. Tokens and credentials use separate HKDF-SHA256
purpose keys derived from `ENCRYPTION_KEY`. Previous-phase token state remains
readable; new token state is versioned. Neither keys nor plaintext passwords live
in SQLite. The UI never displays stored credentials or ciphertext.

**Automatic Campushallen reauthentication requires storing a recoverable encrypted
password. Anyone who fully compromises both the application data and its
encryption key may be able to recover stored credentials.** This is optional per
account. A running application needs plaintext briefly to authenticate; encryption
at rest does not prevent a fully compromised host from reading it.

The worker validates the current token before account operations. Expired/rejected
tokens trigger at most one remembered-password login for that operation. Invalid
credentials latch the account into reconnection-required state, avoiding a retry
loop. Temporary failures back off, with manual intervention after five consecutive
failures. Successful manual reconnection resets the latch. Accounts without saved
credentials require manual reconnection when tokens expire. The existing refresh
token endpoint remains unimplemented; this feature uses a new password login.

`.env`, local YAML, databases, state, logs, tokens, and keys are ignored by Git
and excluded from the Docker build context. Do not share the entire working
directory as an archive. Never enable HTTP debug/body logging with real secrets.

## Website language and localization

The website defaults to Swedish (`sv-SE`, configured as `DEFAULT_LOCALE=sv_SE`).
There is no language selector or browser-language negotiation in this phase.
The CLI, logs, database enums, API values and developer documentation remain English.

Website copy uses standard gettext English source strings and Jinja i18n.
The Swedish catalog is in `src/campushallen/web/translations/sv_SE/LC_MESSAGES/`;
both the editable `messages.po` and compiled `messages.mo` are shipped with the
package. Babel formats Swedish dates and weekdays in Europe/Stockholm; database
timestamps remain UTC. Outcome, account-state and security-event labels are
translated only for display.

After changing website copy, activate the development virtual environment and run:

```sh
make i18n-update   # extract English msgids and update the Swedish .po
# Edit messages.po, translating new messages and checking placeholders/plurals.
make i18n-compile
make test
```

Restart the local app after compiling; catalogs are cached. Commit the `.pot`,
`.po` and `.mo` files together. Future languages such as `en_US` can use another
catalog and a locale-selection policy without rewriting templates. They must
also be enabled in the settings validator; only Swedish is accepted today.
See [localization notes](docs/localization.md) for boundaries and intentional
exceptions.

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
- `cli.py`: local user/admin creation, promotion/demotion, invitation management,
  and YAML import.

See [the current design notes](docs/pre-deployment-design.md) and
[the original audit](docs/development-design.md) for migration choices
and current limits. UTC is stored internally, including SQLite round trips;
business-day matching and displayed times use the configured Stockholm timezone.

## Worker and booking safety

The web server does not start a scheduler. The dashboard reports healthy, stale,
or not-running worker state from a separate database heartbeat. Activity snapshots
show the observed booking/full/waitlist state, API opening time, and next planned
action; they are explicitly cached observations. Run one worker separately:

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
make browser-smoke  # local browser flow, temporary data and mocked Campushallen API
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
See [the current verification record](docs/pre-deployment-validation.md) for tested versions
and smoke-test results.

The Docker smoke check is `docker compose up --build --wait`, followed by
`curl -f http://127.0.0.1:8000/health` and opening `/login`. Avoid running a live
worker during smoke tests. Deployment instructions for the Raspberry Pi and
production hardening will be handled in a later phase.


## Admin setup and invite-only registration

Existing users are **not** promoted by migration. Bootstrap an operator locally:

```sh
campushallen-admin create-user YOUR_USERNAME --admin
# Or promote an existing website account:
campushallen-admin promote YOUR_USERNAME
campushallen-admin demote ANOTHER_USERNAME
```

At least one active admin must remain when demoting or deleting an admin. Admins
can manage invites and inspect safe operational/security status through **Admin**.
Admin status does not bypass account ownership, display credentials, or permit
silent impersonation. CLI commands are a trusted local operator interface.

Generate links ahead of time (or use the Admin page):

```sh
campushallen-admin invite create --label "Friend"
campushallen-admin invite create --count 5 --expires-days 14
campushallen-admin invite list
campushallen-admin invite revoke 1
```

Save the links when printed: plaintext cannot be displayed again. Give each link
to one intended recipient, privately. The database stores only SHA-256 token
hashes. Links are single-use, revocable, and expire after seven days by default;
`--expires-days 0` disables expiry. Generated links use `PUBLIC_BASE_URL`, an HTTP(S)
origin with no path/query/embedded credentials. The development default is
`http://127.0.0.1:8000`; no production domain is assumed.

Recipients choose a website username and password on `/invite/<token>`, then log
in explicitly to start onboarding. Invitation consumption and user creation are
atomic, including concurrent submissions. Invalid, used, revoked and expired
links share an unavailable message. Deleting a user never makes their invite
usable again. Never commit, log, or bake generated links into an image.

## Onboarding and account controls

First login opens a short **Get started** page. Add/connect Campushallen, make an
explicit credential-storage choice, create a first rule, then preview a match.
Every step can be skipped and revisited.

On an account page:

- **Connect/reconnect** verifies a Campushallen login. The remember checkbox is
  unchecked for a new account. Unchecking it also forgets an earlier password.
- **Remember credentials** saves an encrypted password only after successful
  login. Submit a new password with remember checked to replace an existing one.
- **Forget saved password** removes that credential, keeping a current valid
  token. Expired tokens will subsequently need manual reconnection.
- **Disable** stops new work for that connection. Authentication failures show
  whether attention or a timed retry is needed.
- **Disconnect** removes local tokens and remembered credentials, keeping rules
  and history. **Delete connection** removes its local data and booking history.
  Deletion requires website password and explicit confirmation.

Basic rules expose activity/product, weekday, preferred time, facility, waitlist,
and enabled status. Advanced options retain API category/type and other YAML
filters. Leaving the window blank uses an hour either side of the preferred time,
or the full day without a preference; ambiguous matches still fail safely.
Dashboard activity cards offer **Create rule from this activity**, prefilling
product/facility/time rather than hard-coding a future activity ID.

## Website password, sessions, history and privacy

**Security** lets you change the website password, log out all sessions, inspect
your security events, and delete your website account. Website passwords require
12–4096 characters; password managers and long passphrases are supported. Password
change requires the current password and matching confirmation and logs out all
sessions, including the current browser. This is separate from Campushallen
credentials.

History shows account, rule, activity, reason, timestamps and verification status,
with filters for account, result and Stockholm activity date. Security audit events
contain allowlisted action codes and IDs, never passwords, tokens, ciphertext,
cookies, CSRF values or raw API responses. Admins may inspect non-sensitive global
events. In-process rate limiting covers login, invite lookup, registration,
connect/reconnect and sensitive password actions. A shared limiter is required
before running multiple web processes. Form bodies are limited to 256 KiB.

Deleting a rule keeps attempts with a cleared rule reference. Deleting a connection
removes its credentials, tokens, rules, snapshots, claims and local attempts.
Full website-account deletion requires the current website password and typing
DELETE; it removes owned data, sessions and user-linked audit records. An anonymous
deletion event and consumed invite tombstones remain, with recipient references
and recipient labels cleared. The last active admin must create/promote another admin before deletion.
**Local deletion never cancels Campushallen bookings.** An already in-flight
request cannot be recalled.

Notification events are ready for future providers, with a no-op default and a
local log adapter. No email, Discord or Telegram integration sends anything in
this phase. Provider failure cannot undo a committed booking result.

## Upgrade from the previous development phase

Back up the database and matching encryption key, then run:

```sh
alembic upgrade head
alembic check
campushallen-admin promote YOUR_EXISTING_USERNAME
```

Migration `7a36bb9c35dc` preserves existing users, tokens, sessions, rules and
history. Existing users remain non-admin and all accounts remain without saved
passwords until explicitly opted in. Downgrade removes this phase's credential,
invitation, audit, heartbeat and snapshot data; do not downgrade without a backup.
SQLite 3.35+ is needed for downgrade column removal. Losing the encryption key
requires reconnecting accounts and re-entering opted-in credentials.

See [the next-phase pre-deployment checklist](docs/pre-deployment-checklist.md).
That earlier phase deferred deployment. Production Docker, Cloudflare Tunnel,
backup/recovery and the separate future cutover are now documented in the
[deployment runbook](docs/production-deployment.md),
[operations guide](docs/production-operations.md),
[backup guide](docs/backup-restore.md) and [cutover plan](docs/cutover-plan.md).
The runbook distinguishes local verification from work actually completed on the Pi.


For the automated browser smoke test, install Playwright's Chromium once:

```sh
python -m playwright install chromium
make browser-smoke
# Or use an existing Chrome installation:
CAMPUSHALLEN_BROWSER_CHANNEL=chrome make browser-smoke
```

The smoke script starts a temporary loopback server/database, creates temporary
users and an invitation, and mocks every Campushallen call. It exercises admin
invite creation, registration, onboarding, credential opt-in, rule creation,
previews, history filters, credential removal and logout-all. Desktop/mobile
screenshots go to ignored `diagnostics/browser-smoke/`. It never authenticates
against the real Campushallen API or permits booking/cancellation mutations.
