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
| Production schema/image/health | PASS | Deployed c3a1787; a71e420f9c03 head, no drift, integrity/FK clean; all containers healthy |
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
   A pre-incident backup must never erase the spent 4343840 claim.
10. Read dashboard worker health and **Historik** for the phase, HTTP status and
    independent verification. Unknown outcomes require manual review; there is no
    claim reset or cancellation function. Admin shows reconnect and backup status.

## Validation record

Local Python 3.14.4: 228 tests passed, including migration preservation of the
spent incident claim; Ruff and formatting pass, mypy passes for 43 modules. Swedish catalog
contains 489/489 translated messages. Chrome browser smoke passes pending approval,
admin activation and stop as well as the full desktop/mobile user journey. Exact ARM64 image/deployment evidence is recorded below. Automated fixtures prohibit external networking; all
Campushallen mutation requests are mocked. Production read-only checks enforce GET.


## Deployed state, 20 October 2026, 21:17 CEST

Application/source commit: `c3a1787af388d96cc22fb8b3407a217f0a715fbc`.
ARM64 image: `campushallenbot:c3a1787af388d96cc22fb8b3407a217f0a715fbc`,
local image/index digest `sha256:565e43a906861e026293a87a544acb26274e4145d69d0e601baf7c7a811af942`.
Documentation can advance separately. Migration `a71e420f9c03` is at head;
`alembic check`, integrity and foreign keys pass. Migration preserved every
previous row, token, credential and the exact incident history/claim.

The separate worker runs both live flags. Only connection 2 (niklas, user 1)
is eligible, ongoing approval is false, target is **4342668**, total lifetime
claim allowance is **2**, used **1**, remaining **1**. Increasing the total from
1 to 2 grants one additional attempt; no claim was cleared or reset. Claim
4343840 retains its original `2026-10-20 16:45:01.027998` UTC timestamp and history
40 CLAIMED / 41 AUTH_FAILED, with historical phase unknown. No new booking POST
has started, and no cancellation was performed during this phase.

Fresh guarded provider reads confirmed Innebandy 85 Vallastaden, product 1401,
Campushallen/LSIF business unit 1, rooms C-salen/C1/C2/C3/C4. Target **4342668**
starts **29 October 2026 18:45 CET** (17:45 UTC) and opens **22 October 2026
18:45 CEST** (16:45 UTC). It had 15/15 free places and no existing booking.
All three production rule previews matched worker discovery; all 34 guarded GETs
returned 200, with a healthy token and an encrypted saved credential present.
No authentication login or booking mutation was needed for preflight.

The live worker started at 19:15:53 UTC. It independently observed the target at
19:16:59 UTC, with a fresh heartbeat at 19:17:23 UTC. The stored state is waiting,
and planned action equals the API opening, 22 October 16:45 UTC. At that opening
it will authenticate, reselect the same target, check bookings/capacity/policy,
atomically acquire the single remaining claim, issue at most one POST and verify
through an independent GET. Recovery cannot repeat that POST. Unrelated targets
fail the target gate. Niklas remains controlled after this attempt, even if it
succeeds; independently verify the remote booking and local phase before using
the explicit Admin confirmation to approve ongoing Tuesday/Thursday/Sunday rules.

Web, live worker and cloudflared are healthy, non-root, read-only at the root
filesystem, with no published host ports and `unless-stopped` restart policies.
There were zero restarts and no OOM kills. Docker is enabled at boot, user
lingering is enabled, NTP is synchronized to Europe/Stockholm. Disk had 8.2 GiB
free before image staging, and 577 MiB memory was available at the reliability
check. No host reboot was performed under unstable power. Niklas's old timer is
disabled/inactive, its service has PID 0. Friend's timer remains enabled, active
and waiting, and its unit/config/auth files were not edited.

A production temporary invite was created through the trusted operator service,
verified at the canonical public HTTPS URL, revoked, and checked through GET and
a valid-CSRF registration POST: both returned unavailable (400), and no user was
created. The bearer remained only in memory and was not printed. Public Chrome
checks at 1280px and 390px confirmed Swedish login, no horizontal overflow,
Secure/HttpOnly/SameSite=Strict CSRF cookie, strict-origin Referrer-Policy and
private/no-store responses. Authenticated production sessions were not minted or
impersonated for this audit. The full authenticated flow uses mocked local
browser smoke. Additional 390px checks covered onboarding, connection, rules,
manual rule form, history, settings, admin and activation confirmation.

Final verification: **228 tests passed**, Ruff/90-file formatting passed, mypy
passed for 43 modules, 489/489 Swedish catalog entries translated. Both final
Chrome smoke and exact-image production smoke passed. The production smoke also
proved backup with worker stopped, isolated restore, and database/key persistence
across container restart. One existing Starlette/httpx deprecation warning remains.
A targeted Git-history/private-path scan and retained production-log scan found
no high-confidence secrets or known secret-file values; this does not establish
an exhaustive security assessment.

Before migration: `campushallen-20261020T190219412441Z-6b65b1.sqlite3`.
After deployment/approval: `campushallen-20261020T191608307320Z-ac70b0.sqlite3`.
Both passed isolated restore, off-Pi integrity/FK checks and matching separate
0600 recovery-key checks. The latter preserves schema a71, the new finite target
grant and the spent incident claim. The installed daily backup service was run
successfully, including while the worker was stopped; Admin status records
success at 19:15:29 UTC. The timer remains enabled/waiting. Backup retention is
14 completed snapshots; incident evidence lives separately and was not pruned.
Private verification evidence is under
`~/Documents/CampushallenBot-Incidents/corrected-release-c3a1787/`.

To test a selected backup without a running worker, from the Pi release:

```sh
./scripts/production run --rm --no-deps --interactive=false \
  --entrypoint python \
  -v "$HOME/.config/campushallenbot/encryption_key:/run/secrets/encryption_key:ro" \
  -e ENCRYPTION_KEY_FILE=/run/secrets/encryption_key \
  backup -m campushallen.cli restore-test /backups/FILE --expect-user niklas
```

Remaining blockers are **first corrected real booking validation** (including
independent confirmation and no duplicate mutation), **current active undervoltage
and throttling** (`0x50005`: bits 0/2 current, 16/18 historical), and **unverified
router forwarding**. The operator requested continuing work despite power warnings
and reported the router configuration not yet checked. No claim that the site is
ready to share is made. The next API opening is still in the future.
