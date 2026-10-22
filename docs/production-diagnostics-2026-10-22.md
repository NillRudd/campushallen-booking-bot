# Production diagnostics, 22 October 2026

**The normal new-app worker booked regular floorball places for both Niklas and
linus99 at today's opening. Fresh provider reads independently confirm both
bookings. The friend's separate legacy service failed with discovery HTTP 401
before the opening.**

Scope: since the previous check, **21 October 21:28:29 CEST**, with host checks
on 22 October at 20:42–20:46 CEST and independent provider reads at 20:44 CEST.
Application/source/image remains **a95efd7**, schema **c8b31d94a207**. No code,
rules, credentials, live policy, service state or claims were changed by these
diagnostics. No login/booking/cancellation POST, worker invocation, restart,
claim reset or reboot was performed. The ordinary live daemon continued running.

## Actual booking results

Four new-worker booking POSTs were recorded in the interval. All four returned
**201 Created** and passed the engine's independent bookings GET at execution.
The 12 CLAIMED history rows are phase records for these four attempts, not 12
booking requests. Each account/activity pair has one POST-start record.

| Website user / connection | Activity | POST start, Stockholm time | Engine result | Fresh provider evidence |
| --- | --- | --- | --- | --- |
| niklas / 2 | 4356446, Virtual BODYPUMP 45, 22 October 07:00 CEST | 21 October 21:42:31.656 CEST | 201, VERIFIED_BOOKED, history 98 | No longer returned in current or historical-range GET; later disposition unknown |
| niklas / 2 | 4356447, Virtual BODYPUMP 45, 29 October 07:00 CET | 22 October 07:00:00.622 CEST | 201, VERIFIED_BOOKED, history 127 | No longer returned in current or historical-range GET; later disposition unknown |
| linus99 / 3 | 4342668, Innebandy 85 Vallastaden, 29 October 18:45 CET | 22 October 18:45:01.192 CEST | 201, VERIFIED_BOOKED, history 139 | Regular groupActivityBooking confirmed |
| niklas / 2 | Same 4342668 occurrence, separate provider identity | 22 October 18:45:01.521 CEST | 201, VERIFIED_BOOKED, history 141 | Regular groupActivityBooking confirmed |

The floorball opportunity opened at **22 October 18:45 CEST**. Linus's response
was recorded at 18:45:02.374 and verification at 18:45:02.526; Niklas's response
at 18:45:02.754 and verification at 18:45:03.038. Their recorded request intervals
overlap. This is observed normal-scheduler execution for two distinct accounts,
not a fake concurrency test or a manually triggered run. Phase timestamps are
application observations, not packet-arrival measurements.

A fresh client for each account performed customer validation, current bookings
and historical-range bookings GETs: **six HTTP 200 responses**, no login or
mutation. Both actual provider identities are distinct and match their live
reservations. Both floorball bookings are regular places, not waiting-list entries.
Historical-range queries use the official startTimePoint parameter for 21 October.
Absence of the two BODYPUMP records now does not undo their verified execution,
but it does mean they must not be reported as currently held bookings. Their
subsequent cancellation/removal reason cannot be established from these reads.

Current provider bookings:

| User | 25 October, Innebandy | 27 October, Innebandy | 29 October, Innebandy |
| --- | --- | --- | --- |
| niklas | Regular | Waiting list | Regular |
| linus99 | Regular | Regular | Regular |

## Booking-count hypothesis and earlier claims

The captured pre-POST reads for today's floorball showed **three regular entries
for Linus**, and **two regular plus one waiting-list entry for Niklas**. Both
POSTs succeeded. The two BODYPUMP attempts each observed two regular plus one
waiting-list entry and succeeded too.

There were **no new booking POST 4xx/5xx responses, timeouts or verification
failures recorded** in this interval. The safe rejection classifier therefore
has no new live rejection to explain. Today's success validates ordinary opening
execution and regular-place booking, but does not establish a numeric booking
limit or resolve yesterday's badminton 403. See the
[403 investigation](booking-403-investigation.md).

Badminton 4343863 still has its original 21 October 17:15:00.878992 UTC claim,
one POST-start, and no returned provider booking. Original floorball claim
4343840 and the consumed manual approval remain preserved; Niklas's waiting-list
entry for that target is still present. No retry was made by diagnostics.

## Accounts, rules and non-mutation failures

Both website connections are enabled, authenticated, ongoing live, backed by
encrypted remembered credentials, and need no reconnection at the check.
Linus connected on 21 October at 21:50 CEST; owner audit events show automatic
live activation with that successful connection. No operator impersonation or
recipient password creation occurred.

Both users now have three enabled product-1401/unit-1 floorball rules: Tuesday
18:45, Thursday 18:45 and Sunday 12:00. Niklas allows waitlist; Linus does not.
Niklas's rule IDs are now 8–10, Linus's 5–7. Owner audit events show Niklas
updated/deleted/recreated rules; the BODYPUMP/badminton rules are no longer
present. Cleared historical rule references are expected deletion behavior.
Diagnostics did not restore or alter those choices.

Non-preview history also contains:

- One SUSPICIOUS_TARGET discovery result, 22 October 02:00 CEST, safely skipped.
- Two discovery failures, at 09:09 and 11:01 CEST; neither reached a booking POST.
- One authentication-related discovery failure at 16:56 CEST. Its precise
  provider reason/status was not recorded. Owner audit shows successful manual
  reconnection and saved-credential replacement at **17:18:57 CEST**, before the
  successful 18:45 opening. Current reads pass; this is not proof that automatic
  recovery handled that earlier failure.
- Six ALREADY_BOOKED results, correctly avoiding duplicate requests for entries
  already present when new rules were created.
- Two rejected website login submissions at approximately 08:12 CEST, with later
  successful owner logins. Duplicate audit code families are not extra submissions.

The retained worker log has 25,929 lines through collection, all structured INFO,
with four POST-start/acceptance/verified-booked sequences and six already-booked
results. No WARNING/ERROR/CRITICAL structured entries were found. Database
discovery failures above remain reportable even though they do not produce
warning-level log entries; log level alone is not an overall success measure.

## Separate legacy friend: needs attention

The friend's timer is still enabled/active/waiting, but its **last service run
failed**, starting **18:30:39 CEST** and exiting **18:30:42 CEST**, exit status 1.
The user journal records **HTTP 401 Unauthorized during activity discovery**,
before the 18:45 opening. There is no booking-request-start or successful
verification marker in today's retained journal. An enabled timer does not prove
a successful booking.

This establishes an authentication rejection, not whether the token expired or
was otherwise invalid. The friend's provider bookings were not independently
verified with a new login. No conclusion about manual bookings is possible.
Its legacy authentication needs attention before the next run; no password login,
credential/configuration change, service start or new-app migration was attempted.
Niklas's old timer remains disabled/inactive.

## Health, backup and security observations

- Web, worker and cloudflared are healthy, with zero container restarts/OOM.
  Worker remains live with both flags; its heartbeat is fresh. SQLite integrity
  passes with no foreign-key violations and unchanged schema.
- Public HTTPS Swedish login passes at 1280px/390px without overflow, with
  Secure/HttpOnly/Strict CSRF cookie, private/no-store and strict-origin referrer.
  No authenticated website session was created by diagnostics.
- Europe/Stockholm timezone and NTP synchronization are correct. About **473 MiB
  available RAM** and **7.0 GiB free disk** were observed; disk use is 49%.
- **Current undervoltage/throttling remains 0x50005.** Successful bookings do not
  resolve that hardware reliability risk. Router forwarding remains unverified;
  Docker publishes no application host ports.
- The daily backup completed successfully at about **03:31 CEST**. Latest file
  `campushallen-20261022T013059906239Z-b1e35c.sqlite3` is 0600 and passes read-only
  SQLite integrity/FK/schema checks. It predates today's 07:00/18:45 claims; do
  not restore that older snapshot over the current claim ledger as if it included
  today's attempts. No new restore exercise or backup was performed in this
  read-only diagnostic run.
- A targeted scan of retained container logs since the baseline found no known
  secret-file values or scanned bearer/cookie/invite/private-key/credential-body
  patterns. This is not a comprehensive security assessment.

Private sanitized evidence is retained outside Git under
`~/Documents/CampushallenBot-Incidents/diagnostics-20261022-a95efd7/`.
No application changes or deployment were needed. Test suites were not rerun:
this was production observation with guarded GETs and a public browser check.
