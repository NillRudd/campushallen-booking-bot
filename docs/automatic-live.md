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
bookings and waitlist policy, acquires a durable claim, submits at most once and
independently verifies. A previous claim is never reset/replayed automatically.

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

Production deployment and final verification are recorded below after completion.
