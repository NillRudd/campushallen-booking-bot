# Release readiness after the 20 October incident

**NOT READY TO SHARE LINK WITH USERS.** The corrected first real booking has not
been independently verified. Active Pi undervoltage is a separate release blocker.
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
BOOKED from NOT_BOOKED; unreadable state stays UNKNOWN. Both absent and unknown
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
| First corrected real Niklas booking independently verified | BLOCKED | Wait for the next legitimate API opening; never manufacture a test booking |
| No duplicate mutation in corrected real run | BLOCKED | Mocked restart/concurrency/one-POST tests pass; real run still pending |
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
| Production schema/image/health | BLOCKED | Record exact deployed immutable image and migration after deployment |
| Backup/restore/off-Pi recovery | BLOCKED | Record fresh production snapshot, isolated restore and matching private recovery key before deployment |
| Scheduled backup independent of worker | BLOCKED | New network-isolated maintenance container and safe Admin status; verify installed timer execution |
| Secret/privacy audit | PASS | Targeted historical/tracked-file scan and logging review; not a penetration test or proof against every secret format |
| No current active undervoltage | BLOCKED | Pi reports 0x50005: active undervoltage and throttling, plus historical flags; operator requests continuing work |
| Restart and boot persistence | PASS | unless-stopped, user lingering, existing boot evidence; isolated container restart tested again. No new reboot under unstable power |
| Web/tunnel/worker health | BLOCKED | Web/tunnel initially healthy; worker initially deliberately stopped; verify final mode/heartbeat |
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
   A pre-incident backup must never erase the spent 4343840 claim.
10. Read dashboard worker health and **Historik** for the phase, HTTP status and
    independent verification. Unknown outcomes require manual review; there is no
    claim reset or cancellation function. Admin shows reconnect and backup status.

## Validation record

Local Python 3.14.4: 227 tests passed, including migration preservation of the
spent incident claim; Ruff and formatting pass, mypy passes for 43 modules. Swedish catalog
contains 489/489 translated messages. Chrome browser smoke passes pending approval,
admin activation and stop as well as the full desktop/mobile user journey. Exact ARM64 image/deployment evidence is recorded below once its checks finish. Automated fixtures prohibit external networking; all
Campushallen mutation requests are mocked. Production read-only checks enforce GET.
