# Activity discovery and one Campushallen connection

Current release status is maintained in [the release gate and operator guide](release-readiness.md).
Earlier incident-freeze and rollout descriptions below are historical snapshots.

This feature was developed on `feature/activity-discovery` and deployed on
20 October 2026 at `cf4d4d5852a4e1626f9fc4cec9edbf90c44d3cb6`. The operator
explicitly approved resolving the duplicate connections before migration;
connection 2 and all three rules/history were retained. Production used a dry-run
worker at that feature rollout. A later, separately approved Niklas-only live
cutover failed; the worker is now stopped and Niklas's old timer disabled while
the friend's remains enabled. See the
[incident record](incident-2026-10-20-first-live-booking.md) and
[production verification record](production-validation.md) for the API-range
correction and preview/worker parity. The operator subsequently confirmed private
browser acceptance before authorizing the controlled cutover.

## Product flow

Each website account owns zero or one Campushallen connection and many recurring
booking rules. **Mitt Campushallen-konto** shows that connection directly; its
identifier is **E-postadress hos Campushallen**, with an email input and basic
server validation. Existing internal `username` fields and encrypted token state
are retained. A corrected email can be submitted with reconnect; changing the
identity removes the old identity's local tokens/saved credential before login.
An invalid/incomplete form does not discard an existing connection.

**Hitta pass** works after connection without any rule. Search is case-insensitive
and matches partial product/display names, facility and location. It uses a
submitted form, date/range and facility filter. Results are grouped by product and
location; users can select Tuesday, Thursday and Sunday in one action. API capacity,
availability, waiting-list count and API-provided opening time are shown. Missing
opening time remains explicit; the engine never estimates it.

Confirmation creates ordinary independent BookingRule rows. Stable product ID/name,
business unit, location, weekday and exact start time come from normalized API
metadata. The default waitlist policy remains off unless explicitly selected.
Occurrence IDs are only transient selection handles, never rule identities.
Confirmation includes a purpose-separated signed ticket bound to the user,
connection, date range and displayed selector metadata, valid for 30 minutes.
If a session changes product, time or facility after search, confirmation rejects
it and asks for a new search. Availability/opening changes do not silently change
the recurring selectors. Signed selection context supplements existing CSRF and
ownership checks; it never replaces them.
Repeated future weeks collapse to one rule for that weekday/time. Existing manual
rules are recognized through the engine's product/facility filters and linked for
editing. A stable selector fingerprint and unique database index also reject
concurrent duplicate creation.

Manual creation remains available under **Skapa regel manuellt**, with advanced
fields collapsed. The account selector is removed because there is only one
owned connection.

## Browsing, previews and execution

`services/discovery.py` requests a bounded upcoming group-activity range from the
same API endpoint as schedule inspection. ActivityOccurrence contains only
allowlisted metadata, not raw API responses, credentials or tokens. The in-process
cache lasts 60 seconds and holds at most eight datasets of at most 1,000 entries.
The live API rejects a single 21-day period with HTTP 403 while accepting weekly
periods with the same token. Browsing therefore uses at most three contiguous
weekly requests, including across Stockholm's clock change. If the dataset exceeds
the limit, the UI asks the user to select a shorter range. Cache keys separate
account, token-state fingerprint, date and lookahead. Disabled/disconnected
connections cannot use even cached results. Invalid response shapes produce a
schedule error; timestamps without timezone information are rejected.

Browsing is exploratory and does not call booking/cancellation endpoints. It can
perform normal token validation or the existing opted-in reauthentication flow.

A rule's **Matchande kommande pass** calls BookingEngine.discover with a rule
filter. Account preview and the worker use that same function and the existing
strict API selectors. Preview does not use the browser cache. Execution's fresh
selection, opening-time requirement, independent booking checks, waitlist policy,
claims and two-flag live guard remain in place.

Preview history records are marked `is_preview`; they do not overwrite planned
worker actions or count as completed worker attempts. Legacy records have unknown
provenance. Unknown historical dry-run records are safely checked again; all
historical live results continue to count for deduplication. No claims are reset.
Successful discovery replaces a rule's snapshots, removing old matches that have
disappeared. Failed discovery retains explicitly timestamped old observations;
rule preview shows the current failure and guidance instead of an empty section.

## Diagnosed preview failures

The observed production failure was structural: a disabled, unconnected account
owned three rules while a second authenticated account had none. Both selectors
showed the same display name. A preview on the connected account therefore had no
rules to discover. One connection per user prevents recurrence after explicit
operator resolution of existing duplicates.

The audit also found that old previews wrote ordinary DRY_RUN records and cleared
planned actions. The worker could then consider the activity already processed.
Preview provenance now separates observation from worker execution. Successful
refresh also removes stale snapshots, and malformed API payloads no longer turn
silently into empty activity lists. Tests cover these conditions with realistic
Innebandy 85/Vallastaden data.

## Migration and rollout

Migration `e6b41c9a203d` follows `d8426c9ef017`. It checks for duplicate user IDs
before any DDL. Duplicates raise an operator-facing error and preserve all accounts.
A unique index on CampushallenAccount.user_id enforces the one-connection invariant;
using an index avoids rebuilding this SQLite parent table and cascading deletion
of its rules/history. It also adds nullable stable-rule keys and preview provenance.
Existing rules, credentials, tokens, sessions and attempts are preserved. Nullable
historical provenance avoids falsely labelling old worker records as previews.

Before any separately requested production rollout:

1. Inspect the current database read-only for users with multiple connections.
2. If duplicates exist, stop. Review connection identity, credentials, rules,
   history and claims with the operator. Never automatically retain, merge or
   delete an account. Any resolution must be explicit and backed up.
3. Once there are no duplicates, take an online SQLite backup and restore-test it
   with the matching encryption key using the existing production tools.
4. With web/worker stopped, apply Alembic migrations, inspect `current` and run
   `alembic check`. Do not use create_all. Downgrade column removal needs SQLite
   3.35+, as in the previous phase; retain a backup before downgrade.
5. Deploy the reviewed source/image, keep the worker dry-run, and verify the
   browser flow, data persistence, healthy heartbeat and both unchanged live timers.

The operator separately authorized deployment and the specific duplicate
resolution; migration `e6b41c9a203d` is now applied. These steps do not authorize
live cutover or automatic duplicate resolution for another installation.

## Verification

All tests use fake gateways or HTTPX MockTransport; the test fixtures forbid real
network sockets. Coverage includes uniqueness and duplicate-migration refusal,
preservation/upgrade/downgrade, email validation, search without rules, dates and
facilities, Stockholm's clock change, metadata/privacy, error states, multiple
weekdays, stable identities, duplicate protection, matching parity, ambiguity,
full/waitlist state, existing bookings, stale removal and preview/worker isolation.

`make browser-smoke` uses temporary data and mocked Campushallen responses. It
creates Tuesday/Thursday rules together on desktop, a Sunday rule at 390px,
previews both, verifies duplicate handling and checks mobile overflow. Screenshots
are ignored under `diagnostics/browser-smoke/`, including search-desktop.png,
search-mobile.png, rule-preview.png and preview-mobile.png. No real Campushallen
booking or cancellation is performed.

Verified locally on 2026-10-20 on branch `feature/activity-discovery`:

- `make test`: 182 passed; one existing Starlette TestClient deprecation warning.
- `make lint`: Ruff checks and formatting passed (80 files).
- `make typecheck`: mypy passed (42 source files).
- `CAMPUSHALLEN_BROWSER_CHANNEL=chrome make browser-smoke`: desktop and 390px
  mobile journeys passed with mocked Campushallen calls.
- `make production-smoke` using ARM64 image
  `campushallenbot:39a291f4854e597939147121368bde2a23eda01d`: passed migrations,
  model/schema agreement, Swedish login, health, non-root/private networking,
  isolated backup/restore, synthetic encryption checks and restart persistence.
  Temporary containers and network were removed afterward. No tunnel or real
  Campushallen account was configured.

The local checks used Python 3.14.4; the production image uses Python 3.12.
At that initial local verification, deployment was deferred because production
duplicates remained. They were later resolved with explicit approval. The live
API range correction passed 183 tests, Ruff, mypy, browser smoke and isolated
ARM64 production smoke before deployment; the production record documents the
separate rollout and real read-only checks. Legacy timers remain unchanged.
