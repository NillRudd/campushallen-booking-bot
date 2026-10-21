# Controlled live beta: Thursday 22 October 2026

The operator explicitly approved two new beta participants. This is a private,
invite-only beta, not broad public launch. The existing waitlist mutation proof
is recorded in [the client audit](booking-client-audit.md). Power and router
checks remain operational limits; they do not authorize weaker booking safety.

## Intended topology

| Person | Booking source | New-app activation |
| --- | --- | --- |
| Niklas | New worker; legacy Niklas timer disabled | Preserve controlled target 4342668 and its remaining allowance |
| Existing legacy friend | `campushallen-friend.timer`, enabled/active/waiting | Off, including if this person registers for preview |
| Beta A | New worker only after onboarding and review | Explicit per connection; off until reviewed |
| Beta B | New worker only after onboarding and review | Explicit per connection; off until reviewed |

One verified provider customer identity may have only one live booking mechanism.
Email aliases and website/display names are insufficient to establish identity.
No legacy config, password, token or schedule is modified during this beta setup.

## Release preparation status, 21 October

Software changes are prepared locally; **production deployment and fresh Pi/API
checks are pending authenticated SSH access**. The last verified deployed build
is dbcf5a3, schema b2d934a8e710. Do not describe the new identity guard as deployed
until the release/migration and inventory steps below have completed.

| Check | Current evidence / required next step |
| --- | --- |
| Niklas live state | Previously healthy, finite target 4342668, allowance 3 / used 2 / remaining 1; refresh before Thursday |
| Existing friend | Previously enabled/active/waiting; recheck without changes and inventory its real customer identity |
| Beta A/B invitations | Pending production invitation creation; no links in Git, logs or reports |
| Beta A/B onboarding | Pending private invitation delivery and independent registration/connection/rules/preview |
| Beta A/B rule/target/waitlist verification | Pending actual user configuration; do not invent preferences or targets |
| Beta A/B live activation | Off until explicit admin identity/rule review passes |
| Full suite, browser, image smoke | 271 tests, Ruff/format (97 files), mypy (45 modules), 516 Swedish messages and Chrome browser smoke pass; exact ARM64 production smoke pending |
| Pi current power/router | Fresh checks pending; previous power was active 0x50005; router forwarding unchecked |

## Identity enforcement and activation

Migration c8b31d94a207 adds `booking_identities`. It stores an HMAC-SHA256 digest
of the actual provider customer ID using a purpose-specific key derived from the
existing encryption key. Neither raw customer ID, email, credential nor bearer
token is stored in the registry. One digest has one reserved connection; account
reservation is unique. An active external/legacy entry prevents new activation.
An unreviewed identity fails closed at the final mutation claim, even if someone
writes the live flag directly. A changed provider identity cannot inherit the
old reservation. The original activity claims and consumed manual approval are
unchanged and still counted. Key rotation requires re-inventory and review.

Before starting the new image's live worker, inventory the actual Niklas and
friend legacy customer IDs from their configured state, with read-only identity
verification where available. Record Niklas legacy inactive and friend legacy
active through `services.identities.register_external`, using the production
TokenVault key. If identity cannot be established, stop activation for that
identity. Do not print customer IDs, digests or credential material. Record
existing known installations and the operator's review of any other installations;
an application host cannot automatically inspect another person's computer.
Reserve Niklas's existing verified identity through `reserve_identity`, without
changing his live scope, target or allowance. Commit before worker restart.

After each recipient completes setup, Admin → Users and live booking → Review
activation shows enabled rules, product IDs, weekday/time, facility/business-unit
selectors, explicit **Tillåt reservlista: Ja/Nej**, and fresh worker-selector
matches/opening times. Activation repeats the same read-only checks, requires a
recoverable encrypted credential, rejects ambiguous/missing matches or unresolved
claims, and requires both the real-booking confirmation and a separate identity/
rule/waitlist review confirmation. Confirm privately with the participant that
no other legacy/external installation handles that provider identity. Registration
and connection alone never enable live. The unique reservation and live flag
are committed together under SQLite's writer lock; competing alias activations
cannot both succeed. Normal worker claims recheck the actual authenticated
customer identity under that same writer boundary.

An existing friend may use all read-only/new-rule features, but the active legacy
registry entry blocks new-app live activation and shows a Swedish admin warning.
Changing that topology requires separate approval to stop/drain/reconcile the
legacy system first. The registry does not start or stop any external service.
Registry rows with external exclusions survive deletion of a website connection;
its account/admin references are cleared. Downgrade refuses to discard an occupied
registry. Image rollback also requires an identity-aware worker or an explicitly
stopped/reconciled source; an older image does not enforce this guard.

## Multi-user execution and diagnostics

Account operations use separate clients/tokens/credentials. Per-account locks
serialize that account's mutations; different accounts run concurrently under
the existing bounded semaphore (default 8, including discovery). A network request
does not hold a database claim transaction. Local waiting for API opening holds
no thread, database transaction or concurrency permit. Each completed discovery
now queues immediately rather than waiting for every account to finish discovery.

New fake-wire tests cover three independent saved-password logins, simultaneous
18:45:00 opportunities across the DST boundary, overlapping activity IDs with
account-scoped claims/history, bad-password reconnect state, temporary auth
backoff, verification failure and persisted no-replay on worker restart. A POST
barrier proves concurrency rather than relying on timing tolerances. A separate
event-based regression proves slow discovery cannot withhold another account's
due booking. Alias activation races and identity changes fail closed.

Safe structured worker logs include timestamps, account/rule/activity IDs and
phases: discovered, waiting_for_open, pre_auth, claim/auth validated, POST start/
acceptance/rejection and independent verification/result. Admin now shows safe
upcoming action observations and the last 100 non-preview phase records, mapped
to website username, timestamp, activity, HTTP status and result. Owners retain
private history. No password, token, cookie, identity digest, raw API response or
arbitrary exception body is added to logs/history. Observations are cached;
preflight and final mutation selection use fresh provider reads.

## Deployment and Thursday checks

Build and test on the Mac well before the opening. Run `make test`, `make lint`,
`make typecheck`, `CAMPUSHALLEN_BROWSER_CHANNEL=chrome make browser-smoke`, and
`make production-smoke` against the exact ARM64 image. Take a private online
production backup, restore-test it, copy it off Pi and verify the separate matching
recovery key. Commit/push the release. Stop/drain the new worker for migration;
preserve the original claim, history, credentials, tokens and finite scope.
Migrate explicitly, inventory/reserve identities, start services and verify health.
Do not build/test on the Pi near the opening or change router configuration
without evidence of a security problem.

Create two separately labelled, single-use invitations using the production Admin
invitation service and active operator identity. Save each link only in a private
0600 operator file or display it once in the authenticated Admin response. Deliver
privately; recipients choose their own website passwords and Campushallen logins.
Do not create or impersonate their accounts. The operator is authorized to activate
each of these two beta connections only after the actual review passes.

Before Thursday 18:45 CEST, refresh every intended live user's preview using the
same engine as the worker. Record exact account/rule/activity IDs, product/facility,
weekday/time, waitlist choice, API `bookableEarliest`, existing-booking state and
absence of an uncertain claim. Record connection authentication and available
encrypted automatic reconnection. Verify web/worker/cloudflared healthy, worker
both live flags, Niklas legacy disabled and friend legacy enabled/waiting. Check
`vcgencmd get_throttled`: 0x50000 is historical only; 0x50005 includes **current**
undervoltage/throttling (bits 0/2) plus history (16/18). Prominently report current
power risk. No host Docker ports does not prove the router has no forwarding.

Niklas's previously verified target is 4342668, Innebandy 85 Vallastaden,
29 October 18:45 CET, opening **22 October 18:45 CEST** (16:45 UTC). Fresh API data
must reconfirm it is unbooked, unclaimed and matches the exact Thursday rule.
Beta targets must be taken from their own actual choices, not copied from Niklas.
If beta users do not finish onboarding/review in time, report them as **not
activated**; do not weaken defaults or imply the intended four-person topology
is achieved.

Let the ordinary scheduler execute; no manually fired worker, legacy service,
claim reset or cancellation at the opening. Afterwards independently read each
live new-worker identity's provider bookings and compare BOOKED/WAITLISTED/
ALREADY_BOOKED or precise failure against local phases and claims. Inspect the
friend's legacy result read-only. Preserve unknown claims; do not replay POSTs.
Record actual results and power stability before deciding further live scope or
public-launch readiness. Future execution/results cannot be certified in advance.
