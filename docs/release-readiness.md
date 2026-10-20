# Release readiness after the 20 October incident

**NOT READY TO SHARE LINK WITH USERS.** The corrected live waitlist mutation path
is independently production-validated. Active Pi undervoltage remains a release blocker.
Router forwarding cannot be verified from the application host alone.

This record supersedes earlier deployment status summaries. Historical evidence
remains in [the incident report](incident-2026-10-20-first-live-booking.md).
The original provider-side reason for the failed request remains unknown: the
old gateway discarded whether HTTP 401/403 came from POST or verification GET.
The confirmed software defect is loss of phase/status evidence and incorrect
classification of a verification failure as a booking authentication failure.
Current valid tokens and later absence of a booking do not reconstruct that response.

## Corrected behavior

Each new history event carries a timestamp and nullable phase, HTTP status,
endpoint category, and verification outcome. Historical records remain unchanged.
Phases distinguish pre-book authentication, POST started, POST auth rejected,
POST rejected, POST uncertain, POST accepted, verification auth failed,
verification failed, independently booked and already booked. The existing broad
result codes remain compatible with historical data. HTTP status is recorded
when a response exists; a timeout or interrupted process has no response status.
No raw API bodies, URLs, passwords, tokens or exception strings are persisted.

Preflight validates the current token with a customer GET. An expired/rejected
token may use a saved encrypted credential once. The same operation's verification
recovery shares that one-login budget. Invalid passwords latch reconnect-required;
temporary failures back off. POST 401/403 never causes a second POST: the API does
not provide proven safe nonacceptance semantics. After a POST response or timeout,
only an independent bookings GET may reconcile the result. A verification 401/403
may refresh authentication and repeat that GET once. Read success distinguishes
BOOKED, WAITLISTED and NOT_BOOKED; unreadable state stays UNKNOWN. Both absent and unknown
results retain their durable claim. A restart cannot replay the mutation.

Connection approval has two explicit forms: controlled grants with a target and
finite total claim allowance, and ongoing admin approval. Every new connection
starts with both live flags false. Both worker command-line flags remain required.
Admin activation requires a separate confirmation form, CSRF and admin role;
stopping is immediate for new work and records a safe audit event. A controlled
grant can become ongoing through Admin only after a non-preview VERIFIED_BOOKED
result for its selected target. Niklas must remain controlled until then.
Disconnect or identity replacement revokes approval without deleting old claims.

The connect form now checks encrypted password storage by default, with a visible
opt-out and forget action. A submitted unchecked form discards the password and
removes earlier saved credentials. Possession of both data and encryption key
allows recovery; a compromised running host can read credentials during login.

## Release gate

| Requirement | Status | Evidence / limit |
| --- | --- | --- |
| Corrected real Niklas mutation independently verified | PASS | Explicitly approved 4343840: 201 Created, two independent GETs confirm WAITLISTED position 9; scheduled opening-time run still pending |
| No duplicate mutation in corrected real run | PASS | Transport trace: exactly one booking POST, no cancellation; consumed one-use approval plus unchanged original claim |
| Auth phase handling and automatic reauth | PASS | Deterministic Oct 20/27 DST fixture, POST 401/403/400/500, verification 401, timeout, restart, bad-password latch and shared login budget |
| Single connection per website user | PASS | Unique database index, concurrent creation and ownership tests |
| Secure credential storage by default | PASS | Checked connect form, encrypted account-bound credential, replacement/removal tests and browser smoke |
| Website lockout and IP limiting | PASS | Persistent DB account 10/30min with 30min lock, IP 20/15min; generic errors, expiry/reset/restart/concurrency tests |
| Invite-only registration | PASS | Digest storage, single use, revocation, expiry, concurrent consumption and browser smoke; production smoke recorded separately |
| Tenant isolation | PASS | Direct foreign account/rule/history/preview access denied; explicit admin routes only |
| CSRF/session/cookies | PASS | Signed CSRF, strict production Origin/Referer, opaque sessions, secure/HttpOnly/SameSite cookies, logout/password/deletion invalidation tests |
| Hitta pass and shared preview matcher | PASS | Casefold/partial search, weekly API windows, 21 days, facility filters, selection tickets and shared engine preview tests |
| Per-connection live gating; new users off | PASS | Atomic eligibility/claim check; controlled and ongoing approval tests; global dry-run remains authoritative |
| Admin activation/stop and clear user status | PASS | Admin-only confirmed activation, audit, owner dashboard/account/rules pending/active messages; browser smoke |
| Swedish/mobile journey | PASS | Complete mocked invite/onboarding/search/rules/preview/history/security journey at desktop and 390px; no overflow or JavaScript errors |
| Production schema/image/health | PASS | Deployed dbcf5a3; b2d934a8e710 head, no drift, integrity/FK clean; all containers healthy |
| Backup/restore/off-Pi recovery | PASS | Pre/post-deploy online snapshots restored; off-Pi integrity and matching separate recovery key verified |
| Scheduled backup independent of worker | PASS | Installed systemd service succeeded with worker stopped; timer enabled/waiting, safe Admin status successful |
| Secret/privacy audit | PASS | Targeted historical/tracked-file scan and logging review; not a penetration test or proof against every secret format |
| No current active undervoltage | BLOCKED | Pi reports 0x50005: active undervoltage and throttling, plus historical flags; operator requests continuing work |
| Restart and boot persistence | PASS | unless-stopped, user lingering, existing boot evidence; isolated container restart tested again. No new reboot under unstable power |
| Web/tunnel/worker health | PASS | All three healthy, zero restarts/OOM, fresh live heartbeat and target observed after live worker startup |
| Friend legacy timer untouched | PASS | enabled/active/waiting; no service, configuration or credential edits |
| Niklas legacy duplicate source stopped | PASS | campushallen-bot.timer disabled/inactive; service inactive |
| Router has no port forwarding | BLOCKED | No host Docker ports published; router configuration requires operator/router evidence |
| Rollback | PASS | Stop and drain new worker, reconcile all claims remotely, disable connection live, set global dry-run, then consider old timer; never run both |
| Automatic email/Discord/Telegram delivery | NOT APPLICABLE | No-op provider; no messages sent |
| Friend migration | NOT APPLICABLE | Separate future controlled process |

## Operator workflow

Do not distribute invitations until all release blockers are resolved.

1. In **Admin**, create one invitation and send its link privately.
2. The recipient registers, connects Campushallen, uses **Hitta pass**, selects
   sessions, creates rules and previews **Matchande kommande pass**.
3. Check account health, rule choices and preview with the recipient. **Admin →
   Användare och livebokning → Granska aktivering av livebokning** opens a separate
   confirmation explaining real mutations. Explicitly confirm and activate.
4. Users see pending/active status on overview, account and rules. Global worker
   health remains visible separately; approval alone does not start a worker.
5. Emergency stop one connection: **Admin → Stoppa livebokning**, or its owner
   disables/disconnects the connection. An in-flight request cannot be recalled.
6. Stop all new-worker work on the Pi: `cd ~/campushallenbot &&
   ./scripts/production stop worker`. It drains in-flight work; never kill/restart
   blindly around an uncertain result. This does not touch the friend's timer.
7. Rollback Niklas: keep new worker stopped; inspect current remote bookings and
   all claims, disable its app live approval and set WORKER_MODE=dry-run in the
   private production.env. Only after reconciliation consider
   `systemctl --user enable --now campushallen-bot.timer`. Retain old files; do not
   enable it alongside the new live worker. Friend remains unchanged.
8. Backup: `./scripts/backup-production` works even with worker stopped. The
   daily user timer runs the same helper. It uses SQLite's online backup, keeps
   14 completed snapshots, and records success/failure for Admin. Check
   `systemctl --user status campushallen-web-backup.timer` and
   `journalctl --user -u campushallen-web-backup.service` for failures. A successful
   result older than 26 hours displays overdue.
9. Restore test: use a private isolated container with the backup directory
   writable for temporary verification and the recovery encryption key mounted
   read-only, running `python -m campushallen.cli restore-test /backups/FILE
   --expect-user niklas`. Copy verified snapshots off Pi; retain the matching
   encryption key separately with mode 0600. For a real restore, stop web/worker,
   preserve the current DB and claims first, restore a consistent snapshot and
   reconcile every live claim with Campushallen before any booking source resumes.
   A pre-incident backup must never erase the spent 4343840 claim or the
   consumed reconciliation. Downgrade refuses to discard consumed approvals;
   any rollback worker must account for that additional spent allowance.
10. Read dashboard worker health and **Historik** for the phase, HTTP status and
    independent verification. Unknown outcomes require manual review; there is no
    claim reset or cancellation function. Admin shows reconnect and backup status.

## Current deployed state, 20 October 2026, 21:56 CEST

Application/source and image tag: `dbcf5a33fc335f1db974d2afff95100b928c631a`.
Image ID: `sha256:c6b48ab0ae275117ce621630d2ee095df4e3fafa17c42fe31f0f25dfbdb3d1c1`.
Documentation can advance separately. Schema `b2d934a8e710` is at head;
Alembic drift, integrity and foreign-key checks passed. Migration preserved old
rows, tokens, credentials and the exact original incident claim/history.

See [the full old/new wire audit, selectors and actual test](booking-client-audit.md).
At 21:53 CEST the separately approved one-use reconciliation for 4343840 sent
**one POST**, received **201 Created**, and independently confirmed **WAITLISTED
at position 9** twice. The final local event is VERIFIED_WAITLISTED / WAITLISTED
(history 55); the original claim and historical AUTH_FAILED event are unchanged.
No cancellation occurred. The prior failure's provider-side cause remains unknown.
A separate real saved-password login and authenticated reads succeeded before
this test; no login was needed during the booking operation itself.

The healthy worker uses both live flags. Only connection 2 (niklas, user 1) is
eligible. It is restored to target **4342668**, ongoing approval **false**,
lifetime allowance **3**, used **2**, remaining **1**. Usage counts the original
claim and consumed manual approval; neither was reset. The next target starts
**29 October 18:45 CET** and opens **22 October 18:45 CEST**. Stored planned action
matches the API opening. At that opening the normal worker will reauthenticate
if needed, reselect, duplicate-check, enforce waitlist policy and atomically
reserve the one remaining mutation before an at-most-once POST and verification.
Keep this scope until the scheduled opening-time run is independently verified;
ongoing Tuesday/Thursday/Sunday approval is deliberately not enabled yet.
All three rules now use exact product 1401, business unit 1, Vallastaden and
fixed Tuesday/Thursday 18:45 / Sunday 12:00; enabled/waitlist choices are unchanged.

Web, worker and cloudflared are healthy, non-root, read-only at the root
filesystem, with zero restarts/OOM, no published host ports and unless-stopped
restart policies. Niklas's old timer is disabled/inactive, service PID 0;
the friend's remains enabled/active/waiting and its installation is untouched.
The daily independent backup timer remains enabled/waiting. No reboot occurred.

## Verification and recovery

Local Python 3.14.4: **256 tests passed**, Ruff/93-file formatting passed,
mypy passed for 43 modules, and 494/494 Swedish catalog entries are translated.
Final Chrome browser smoke and exact ARM64 production image smoke passed.
New tests cover one-use reconciliation, unchanged claims, concurrency, quota,
expiry/identity/admin/scope checks, restart/unknown outcomes, CLI execution,
disconnect, downgrade preservation and waitlist classification. Automated tests
forbid external networking; the explicitly authorized real test was separate.
One existing Starlette/httpx deprecation warning remains.

Before this migration: `campushallen-20261020T194620509296Z-ace0c3.sqlite3`.
After the successful real test: `campushallen-20261020T195519366647Z-12b3d4.sqlite3`.
Both passed isolated restore, off-Pi integrity and matching separate 0600
recovery-key checks. The latter also verifies consumed approval 1, original
claim/history, WAITLISTED result and the restored finite future target scope.
Daily backup status remains successful. Private evidence is outside Git in
`~/Documents/CampushallenBot-Incidents/live-waitlist-dbcf5a3/`.

Retained production logs contained no known secret-file values or scanned
bearer/cookie/invite/password patterns. Targeted history/private-path scanning
and logging review are not an exhaustive security assessment. Earlier public
HTTPS/mobile/cookie/invitation acceptance remains recorded in the production
validation notes; final authenticated browser smoke used mocked Campushallen.

Never restore an older snapshot over spent claims or consumed manual approvals.
Schema downgrade intentionally refuses to erase consumed approvals, and older
worker images do not count this ledger: use a schema-compatible worker for image
rollback. To return to legacy, stop/drain the new worker, reconcile remote state
including the 27 October waitlist entry, disable app live approval and set global
dry-run before considering the old timer. Never run both booking sources.

## Remaining release blockers

The live mutation path is now validated for a real waitlist entry. Regular-place
booking and the next opening-time run have not yet been observed with this build.
The public-launch blockers remain **current/recurring active undervoltage** and
**unverified router forwarding**. A brief earlier reading was `0x50000`
(historical only), but the final 21:56 CEST reading is **0x50005**: bits 0/2 are
current undervoltage/throttling, bits 16/18 historical undervoltage/throttling.
The operator requested continuing work despite power warnings. No published
Docker ports does not prove the router has no forwarding rules.
