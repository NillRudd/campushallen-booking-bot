# Automatic live booking

On 21 October 2026 the operator explicitly replaced the controlled-beta admin
activation policy with **automatic ongoing live booking after connection**.
Recipients still register by invitation and choose their own Campushallen login.
A successful connection enables automatic booking for that owner's enabled rules;
no admin approval, first test booking, finite allowance or selected-activity gate
is required. This supersedes the earlier beta activation instructions.

Users decide which sessions to add and whether each rule is enabled. **Pausa
automatisk bokning** pauses the connection; **Återuppta automatisk bokning**
resumes ongoing live without admin access. A paused/disabled connection stays
paused on reconnect. A background token refresh never enables a stopped account.
Admin stop remains available; the owner can explicitly resume their own account.
Rule waitlist preferences are unchanged and are never automatically enabled.

The application shows **Automatisk bokning: Aktiv / Pausad**, or asks the user to
connect Campushallen. Connection and rule forms explain that enabled rules lead
to real bookings. Web requests still do not execute bookings: the separate worker
runs with both live flags and uses the API opening times. Preview remains read-only.
Saved credentials remain optional, encrypted by default; opting out requires
manual reconnection after token expiry and does not prevent live while valid.

The previous admin blockers were the unverified selected test target and a
connection-wide preview prerequisite. Missing/ambiguous matches, absent future
schedule data or one unresolved historical claim no longer prevent other safe
rules from running. Those diagnostic warnings remain visible; each actual
operation still reselects, validates identity/opening/time/facility, checks
bookings and waitlist policy, acquires a durable claim and independently verifies.
The [22 October reliability policy](booking-reliability.md) permits at most three
POSTs within that original operation only for an explicit provider booking-limit
rejection followed by verified absence and a count below four. All unknown
outcomes remain one POST; a previous claim is never reset/resumed automatically.

Actual authenticated provider identity remains reserved to one new-app live
connection. The registry is maintained atomically; owner and admin actions check
ownership and active-user status. Existing legacy inventory is retained and the
old installations/timers are not modified. A matching known active legacy source
still cannot accidentally duplicate a new-worker booking mechanism. The existing
legacy friend has no website connection, so does not affect current users.

Existing authenticated, enabled connections are transitioned to ongoing live
once during this deployment. Unconnected/reconnect-required users cannot book
until they connect successfully, at which point activation is automatic. Disabled
connections remain paused. Historic claims, consumed approvals, quota metadata,
credentials, tokens, rules and history are preserved; ongoing authorization
explicitly supersedes the old finite scope. No schema migration is needed.

Tests exercise ordinary non-admin connection with and without saved passwords,
owner pause/resume, foreign-account denial, paused reconnect, ongoing booking
beyond old finite restrictions while retaining old claims, admin recovery,
per-account identity collisions and the existing three-user concurrency/failure
suite. The browser smoke verifies automatic live after connection and owner
pause/resume without any real booking traffic.

## Deployed, 21 October 2026

Application/image `ad8e3fbdfc1e7d8b0403ffe98175d7e2e4031844` is deployed;
schema remains c8b31d94a207 with no drift. Web, live worker (concurrency 3) and
tunnel are healthy. Niklas connection 2 is now ongoing live for all four enabled
rules. The original finite metadata, historic claim and consumed manual approval
are unchanged; no old activity is retried. Linus99 has not yet established a
working Campushallen connection, so needs to connect once; that successful
connection will enable live automatically without admin action.

The worker is queued for **4343863, Badminton 70 Vallastaden**, starting
**28 October 19:15 CET**, opening **21 October 19:15 CEST**. It is also queued for
the existing Thursday floorball opening on 22 October. A separate read-only
monitor on the Pi watches the normal worker and independently verifies the
badminton result afterward; it cannot POST a booking or cancellation.

Validation: **278 tests**, Ruff/format (99 files), mypy (45 modules), 519/519
Swedish translations, full Chrome browser smoke including automatic activation
and owner pause/resume, and exact ARM64 production image smoke all pass. Public
HTTPS login also passes at desktop and 390px. After deployment, 21 guarded
provider GETs returned 200; verification sent no booking or cancellation request.
One Tuesday occurrence has a SUSPICIOUS_TARGET selection warning; it remains
safely skipped without blocking the other rules or the badminton opening.

Pre-deploy backup `campushallen-20261021T164545952477Z-82b6b8.sqlite3` and
post-deploy `campushallen-20261021T165221580733Z-b324aa.sqlite3` passed isolated
restore, off-Pi integrity and matching separate recovery-key checks. Original
claims, consumed approvals, tokens, credentials, rules and history were preserved.
Niklas's old timer remains disabled; the friend's remains enabled and untouched.
No host reboot or Pi-side build/test was performed. Active undervoltage/throttling
still reports 0x50005; router forwarding remains unverified.

## Scheduled result and final release

The worker executed at **19:15:00.95 CEST**, but the booking POST received **403**.
Its verification and a separate fresh-client GET confirmed **NOT_BOOKED**.
The claim is retained, with one recorded POST start and no automatic retry or
cancellation. Basic read-only membership/terms/validity/attestation/clock checks
did not establish the provider's exact rejection reason. See
[the scheduled-attempt report](incident-2026-10-21-badminton.md). This test must
not be described as a successful regular booking.

After preserving the attempt, final application/image
`b29e631c5111259fecd4f055984ed6493eaf59c3` was deployed. It also fixes a waiting-job
pause/resume edge case: account/rule reactivation or successful pre-book
reconnection can reschedule a job that stopped before mutation. Claimed or
uncertain outcomes remain protected by persisted deduplication; the failed
badminton activity is not replayed. **281 tests**, Ruff/format (100 files), mypy
(45 modules) and exact-image production smoke pass; the prior full automatic
live/owner pause-resume browser smoke remains applicable to the unchanged UI.
Web, live worker and tunnel are healthy with zero restarts/OOM. Schema c8b31d94a207,
concurrency 3 and all existing ongoing flags/data are preserved. Floorball
4342668 remains waiting for 22 October 18:45 CEST. Linus99 still needs a successful
Campushallen connection, after which live will start automatically.
