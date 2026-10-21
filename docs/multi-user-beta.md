# Controlled live beta: Thursday 22 October 2026

**Current policy update, 21 October:** the operator now requests automatic ongoing
live booking after successful connection, with owner pause/resume and no separate
admin activation. See [automatic live booking](automatic-live.md). Earlier
controlled-scope and per-user admin-approval instructions below are historical.


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

**Deployed and ready to send the two private invitations.** The intended
three-user new-worker topology is **not yet active**: Beta A and Beta B have not
onboarded. The operator will send links after this preparation and confirms
neither has another booking bot; the legacy friend will not onboard for now.
Actual beta connections/rules/preferences still need individual review and
activation after setup. No accounts or passwords were created for recipients.

At **17:03 CEST on 21 October**, application/source/image tag
`a9e7321675c9b7e2e55db107f76b97268838246a` is deployed, schema **c8b31d94a207**.
Image ID is `sha256:22eaf5607b70581d85a44890fb9353321527f5627e9f50ab3b0a122f6903b2fb`.
The source includes the identity guard from 9793760 and production concurrency 3.
Documentation and test-only fixes may advance separately.

| Check | Verified state / remaining work |
| --- | --- |
| Niklas | Connection 2, authenticated, one connection, recoverable encrypted credential, controlled live on; ongoing off |
| Niklas rules | Product 1401 / Innebandy 85 Vallastaden, unit 1, fixed Tue/Thu 18:45 / Sun 12:00, all enabled, waitlist explicitly yes |
| Niklas target | 4342668, 29 Oct 18:45 CET; opens 22 Oct 18:45 CEST; 15 free, no booking or claim, preview equals worker |
| Niklas allowance | Lifetime 3, used 2, remaining 1; original claim and consumed reconciliation preserved |
| Existing friend | Legacy timer enabled/active/waiting, fresh identity GET 200, distinct from Niklas; active legacy registry exclusion, no website connection |
| Beta A | Single-use invitation 4 created, unused/unrevoked; onboarding, rules, waitlist choice, targets and explicit live activation pending |
| Beta B | Single-use invitation 5 created, unused/unrevoked; onboarding, rules, waitlist choice, targets and explicit live activation pending |
| New-user defaults | Live off; only connection 2 currently eligible; registration remains invite-only |
| Worker | Healthy, both live flags, concurrency 3, fresh live heartbeat, target waiting at API opening |
| Web/tunnel | Healthy; public Swedish HTTPS login passes at 1280px/390px, no overflow; secure/HttpOnly/Strict CSRF cookie and private/no-store response |
| Multi-user tests | Three simultaneous independent logins/claims/history, permanent auth failure, temporary backoff, verification failure, slow discovery, alias races and restart/no-replay pass |
| Full checks | 271 tests; Ruff/format 98 files; mypy 45 modules; 516/516 Swedish messages; Chrome browser smoke and exact ARM64 production smoke pass |
| Pi power | **0x50005: current undervoltage/throttling**, plus historical flags; reliability risk remains |
| Clock/resources | Europe/Stockholm, NTP synchronized; no container restarts/OOM, about 561 MiB available at the check |
| Router | Read-only SSDP/IGD probe found no discoverable gateway; forwarding remains unverified. No Docker host ports published; no network changes |

Invitation links are stored only in private files
`~/Documents/CampushallenBot-Beta/beta-A-invitation.txt` and
`beta-B-invitation.txt` (directory 0700, files 0600). They expire on
**28 October at approximately 15:53 CET** and were created by the active Niklas
admin through the same production invitation service used by Admin. No link or
token is included here, in logs or in Git. Recipients choose their own passwords.

Before migration, `campushallen-20261021T145516320021Z-6c0b0b.sqlite3` passed
isolated restore, off-Pi integrity and matching separate recovery-key checks.
After deployment, `campushallen-20261021T150310607811Z-f8ac12.sqlite3` passed the
same checks and preserves both identity exclusions/reservations, the original
claim/history, consumed approval, verified waitlist result and finite target.
The daily backup timer remains enabled/waiting and its last scheduled run
succeeded. Migration preserved all existing tokens, credentials, claims,
reconciliations and live scope exactly. Legacy config/credential-file fingerprints
and encryption/session/tunnel keys were unchanged. No host reboot was performed.

After deployment, **23 guarded provider GETs returned 200**, with no login needed,
no booking POST and no cancellation. Fresh selection and preview agree, and the
worker independently stored the same next opening. The only BOOK_POST_STARTED
in the database remains the separately approved 20 October waitlist test. Retained
container-log scanning found no known secret-file values or scanned bearer,
cookie, invite, private-key or credential-body patterns; this is not an exhaustive
security assessment. Private evidence is outside Git in
`~/Documents/CampushallenBot-Incidents/multi-user-beta-a9e7321/`.

Remaining causes of a missed Thursday booking include **active Pi undervoltage/
throttling**, incomplete beta onboarding/activation, provider/network latency or
rejection, changes to availability or selection, and unknown router forwarding
configuration. Tests cannot guarantee provider acceptance or timing under actual
hardware/network load. No build or test was run on the Pi; images were built and
smoke-tested locally well before Thursday's opening. The final preflight on
Thursday and independent post-run verification remain future work.

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

For this deployment, the actual identities were inventoried and registered
before starting the live worker. For future installations, inventory the Niklas and
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
the bounded semaphore (production 3 for Niklas plus Beta A/B; development default
8, including discovery). A network request
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
