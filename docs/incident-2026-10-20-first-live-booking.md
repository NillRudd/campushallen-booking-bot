# First Niklas live attempt: evidence and investigation limit

## Current disposition

**Remote absence verified. New worker stopped. Claim and allowance preserved.
Exact API rejection cause is not recoverable from the recorded evidence.**

The operator reported that activity 4343840 was not booked and explicitly
prohibited another POST, claim/allowance reset or cancellation. This investigation
captured the existing process/log/database evidence before stopping the worker.
No source fix, deployment, retry, cancellation or legacy-timer change followed.

Production source/image before and after investigation remains
`c981a44e35d5c14c1d5654d7045343766225d68d`; schema is `f2c9a71e6b04`.
Web and cloudflared remain running/healthy. The worker container is deliberately
exited (exit 0), retaining its two-flag live command but with no running process.
Niklas's old timer remains disabled/inactive, service inactive/MainPID 0. The
friend's timer remains enabled/active/waiting. All four unit files match the
pre-cutover fingerprints; neither legacy checkout/config/auth was changed.
Niklas therefore currently has **no running automatic booking mechanism**.

## Preserved evidence

The private Pi directory is
`/home/niklas/.cache/campushallen-incidents/activity-4343840-kofx30t9`.
It contains selected pre/post-stop container state, the timestamped worker logs
for 18:00–19:30 CEST, subsequent logs and an online SQLite snapshot taken before
stopping. The snapshot passed integrity and foreign-key checks. Five files were
copied privately off-Pi to
`~/Documents/CampushallenBot-Incidents/activity-4343840-kofx30t9/`; its snapshot
also passed those checks. Directories are 0700, files 0600. No encryption key is
included, and no raw evidence/database/credential belongs in Git.

The pre-cutover backup/key recovery set remains available, including verified
snapshot `campushallen-20261020T155843728654Z-be9e48.sqlite3`. Restoring that older
snapshot could discard the later live claim; it must not be used to retry blindly.

## Timeline (20 October, Europe/Stockholm / CEST)

| Actual timestamp | Evidence |
| --- | --- |
| Before 18:04:41 | Cutover audit/command results proved old Niklas timer disabled/inactive, service inactive; friend enabled/waiting before live activation. |
| 18:04:45.231 | Final live worker container started on c981a44. RestartCount stayed 0; OOMKilled=false. |
| 18:05:04.694 | Live worker-state row started. The observed command contained both real-booking flags. |
| 18:05:31 | Recorded scope check showed only connection 2 eligible, activity 4343840, limit 1, no claims or live target attempts. Read-only preflight found the intended target unbooked with 15 regular spots. |
| 18:45:00 | API-provided opening, persisted as 16:45 UTC. Exact scheduler wake-up/request-start timestamps were not logged. |
| 18:45:01.027998 | Durable claim row timestamp for connection 2/activity 4343840. It committed before the following reservation record. |
| 18:45:01.067174 | History row 40: CLAIMED, is_preview=false. |
| 18:45:02.989289 | History row 41: AUTH_FAILED, is_preview=false; generic message “Reconnect this Campushallen account.” |
| 18:45:03.028467 | Structured worker log: Booking attempt completed, account 2, rule 1, AUTH_FAILED. |
| 20:35:17.897234 | Latest heartbeat in pre-stop snapshot: live mode. Historical heartbeat cadence is not retained as a time series. |
| 20:35:21.321 | Container state/evidence capture began: running, healthy, no restart/OOM. Logs and consistent DB snapshot preserved before stopping. |
| 20:35:24.796 | Worker finished gracefully with exit 0 after investigation freeze. No restart was performed. |

The friend service's recorded natural run was 18:30:27–18:45:01, Result=success.
It was not manually triggered or modified during either investigation or cutover.

## Confirmed local and remote state

- Connection 2/user 1 is enabled, connected and not reconnect-required. A saved
  encrypted credential exists. Last auth success remains 16:08:33 CEST; no auth
  failure/backoff/reauth audit event is recorded in the incident window.
- Live eligibility remains true, approved activity 4343840, total allowance 1,
  durable claims 1, **remaining allowance 0**. No field or claim was reset.
- All three rules remain enabled. Their last updates predate the booking window.
  No audit event indicates a disablement or identity change during the window.
  No separate identity fingerprint is stored: the row-bound grant is revoked on
  disconnect/email replacement. Claim acquisition also rechecks active ownership.
- Tuesday rule 1 selects product Innebandy 85 (actual API name Innebandy 85
  Vallastaden, product 1401), weekday 1, preferred 18:45, window 17:45–19:45,
  facility filter Campushallen, category 40/group_activity. Fixed time is unset.
  Existing waitlist opt-in is true on all three rules.
- Target 4343840 starts 27 October 18:45 CET (17:45 UTC). Its API opening was
  20 October 18:45 CEST (16:45 UTC). Business unit is Campushallen/LSIF, rooms
  C-salen/C1–C4. Fresh production selection still returns that exact target.
- Independent customer, bookings and schedule GETs all returned HTTP 200 using
  the existing token, without reauthentication. The bookings GET contains **no
  booking or waiting-list record for 4343840**. Current availability is 0/15,
  waiting list enabled, waiting count 8. Counts are observations, not opening-time
  history. No non-GET Campushallen call was made during this investigation.
- Preview/history rows for this target: row 8 DRY_RUN with unknown legacy
  provenance; rows 21 and 31 DRY_RUN with is_preview=true; row 40 CLAIMED and row
  41 AUTH_FAILED with is_preview=false. No BOOKED result exists for this target.
- The opportunity shows already_booked=false, worker_status=auth_failed and no
  next action. History/dashboard data therefore do not indicate success.
  Authenticated browser rendering was not accessed or impersonated.

## Failure-mode audit

| Possibility | Finding / limit |
| --- | --- |
| A. Scheduler never processed rule | Ruled out: target-specific claim/history and worker event exist at opening. Exact wake-up and API-call times were not logged. |
| B. UTC/Stockholm error | Stored opening 16:45 UTC = 18:45 CEST; start 17:45 UTC = 18:45 CET on 27 October. Claim follows the correct opening by about one second. No observed one-hour/week/day error. |
| C. Live gate blocked execution | Ruled out before reservation: acquiring this claim required both account eligibility and reviewed target/allowance checks. Actual command had both flags. The grant remains unchanged and no revocation event is recorded. |
| D. Matcher/target mismatch | Fresh exact production matcher returns the reviewed activity/product/day/time/facility. Claim-path code requires a fresh unique selection and matching queued ID/start before claiming. Individual request payloads at opening were not recorded. |
| E. Authentication failure | AUTH_FAILED is recorded, but preflight must have progressed past customer/selection/bookings reads to obtain the claim. The same token is currently valid and no reauth/failure latch is recorded. This does not prove why a particular write/verification endpoint rejected it at opening. |
| F. Preview/history suppression | Ruled out for this attempt: previews and legacy DRY_RUN records existed, but live execution still acquired the claim. They did not spend the grant. The new claim now blocks blind retries as designed. |
| G. Premature allowance consumption | Claim was created after opening and immediately before the mutation stage. Discovery/preview/auth did not consume it. Reservation intentionally precedes POST; a rejection/uncertain outcome keeps it spent. Consumption itself does not prove acceptance by Campushallen. |
| H. Capacity/waitlist safe skip | No SKIPPED_WAITLIST_ONLY/FULL outcome occurred. Reaching the claim means local capacity/waitlist checks passed. Exact availability at the opening was not retained; present availability cannot reconstruct it. |
| I. API/network failure | In the deployed code, AUTH_FAILED after CLAIMED can arise when AccountGateway converts a 401/403 HTTPStatusError during booking POST or subsequent verification GET into ReconnectRequired. The exact status, request stage, response code/reason and correlation ID were discarded. DNS/timeouts would follow a different result path, absent another unrecorded defect. |
| J. Startup/restart timing | Worker started roughly 40 minutes before opening, live state was observed beforehand, and container reports zero restarts/OOM. There is no evidence of a late startup or crash causing this miss. |

## What can and cannot be concluded

The deployed code places the booking POST after the claim/reservation record;
the subsequent AUTH_FAILED path comes from a 401/403 on that POST or its
independent verification GET. This is code-path reconstruction, not an HTTP
transaction log. **The POST's server receipt/acceptance cannot be established.**
It may have been rejected, or it may have returned before
the verification GET failed. Present remote absence does not recover the original
response or prove the history of remote acceptance/removal.

The application collapses those two cases into the same misleading auth label
and discards the HTTP diagnostic evidence. That is a confirmed diagnostic and
outcome-classification limitation, **not a proven explanation of Campushallen's
underlying rejection**. Token rejection, write authorization/business validation
and verification-endpoint rejection cannot be distinguished retrospectively.

The investigation stops at the operator's “insufficient evidence” condition.
Missing evidence is the original failing method/stage, exact HTTP status and a
sanitized service error/correlation identifier. There is no defensible causal
regression test for the original server rejection yet. No speculative fix/test
was added or deployed; the existing 205-test baseline is unchanged. A future
diagnostic change should preserve allowlisted stage/status/error metadata and
retain uncertain claims, but cannot retroactively establish this incident's cause.

## Next candidate and rollback readiness

Read-only fresh discovery identified the next unopened opportunity:

| Field | API observation |
| --- | --- |
| Rule / activity | Thursday rule 2 / 4342668 |
| Product | Innebandy 85 Vallastaden, product 1401 |
| Start | Thursday 29 October 2026, 18:45 CET |
| Facility | Campushallen/LSIF, C-salen/C1–C4 |
| bookableEarliest | Thursday 22 October 2026, 18:45 CEST |
| Booking / availability | Not booked; 15 regular spots observed |

This is a **candidate only**. No target/allowance update or real attempt is
authorized or activated by this investigation. Do not run `up worker` to resume
the old live configuration, and do not clear claim 4343840.

Old Niklas files/units remain intact. If rollback is explicitly chosen, keep the
new worker stopped, reconcile uncertainty independently, disable its live grant
and select dry-run configuration before enabling only the old Niklas timer.
The friend is never part of rollback. No automatic rollback or reboot occurred.

The daily backup helper executes in the worker container and cannot succeed while
that container is stopped. Do not restart the live worker merely to run a backup.
The verified private incident snapshot and existing recovery set are retained;
arrange an isolated CLI/host online backup if the freeze continues across the
next scheduled backup. No backup timer was modified in this investigation.

**No real booking POST or cancellation was performed during this investigation.
No claim/allowance was reset. No friend configuration was changed.**
