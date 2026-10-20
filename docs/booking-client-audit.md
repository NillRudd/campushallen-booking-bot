# Legacy/new booking client audit and approved waitlist test

Audit performed on 20 October 2026 against the installed Niklas legacy source
`/home/niklas/booking_bot/src/campushallen/` and deployed application c3a1787.
The friend's installation, configuration and credentials were not inspected or
changed. No cancellation is authorized.

## Exact request construction

The installed legacy module path and HTTPX version were checked in its actual
venv. Its login and booking methods were exercised with transport intercepted:
zero network mutations. The new production client was inspected after real
current-token and saved-password authentication; its booking request was built
but not sent during the audit. The legacy and new customer identities and login
email match, without printing either token or password.

| Aspect | Working legacy Niklas bot | New gateway/worker |
| --- | --- | --- |
| API base | `https://campushallen.brpsystems.com/brponline/api/ver3/` | Same |
| Login | POST `/auth/login`, JSON `username`, `password` | Same fields and same email; clean login client |
| Token/customer extraction | `access_token`; login `username` converted to integer customer ID | Same; actual customer identity matched |
| Token type/header | Hard-coded `Bearer` prefix | Same; real refreshed token reports Bearer |
| Saved browser cookies | No saved cookies in installed legacy client | Shared legacy files deliberately not loaded |
| Live cookie handling | HTTPX cookie jar retains responses within its client | Same HTTPX cookie jar; authenticated GET acquired a cookie; booking client retained it |
| Client/session reuse | One HTTP client across refresh/duplicate check/POST/verification for a rule | Same across an operation; authentication helper uses its own login client, as legacy login command also did |
| HTTPX/redirects/timeout | 0.28.1, redirects enabled, 30-second timeout | Identical |
| Accept | `application/json, text/plain, */*` | Identical |
| Accept-Language | `sv-SE,sv;q=0.9,en;q=0.8` | Identical |
| Content-Type | JSON POST automatically uses `application/json` | Identical |
| Referer | `https://campushallen.goactivebooking.com/` | Identical |
| Origin | Not set | Not set |
| User-Agent | `Mozilla/5.0 CampushallenBookingBot/0.1` | Identical |
| Request source | `X-REQUEST-SOURCE: mobilityweb` | Identical |
| Booking | POST `/customers/{customer}/bookings/groupactivities` | Identical |
| Booking JSON | `groupActivity` integer; `allowWaitingList` boolean | Identical format and serialization |
| Waitlist policy value | Actual legacy configuration is false | Actual three web rules are true; this is an explicit policy difference, retained |
| Facility/type in POST | No separate facility or category field; group-activity endpoint plus selected activity ID | Identical; facility/category are selection constraints |
| Target refresh | Refresh same selected ID before POST | Re-select and require unchanged ID/start, exact selectors and API opening |
| Verification | Authenticated GET to same bookings path; enabled in installed config | Same independent GET; mandatory, with safe GET-only reauthentication |
| Unknown HTTP result | Legacy exception/unknown result, no durable claim | Original durable claim retained; no automatic mutation replay |

No demonstrated new request-construction defect explains the historical 401/403.
The old handler's phase loss still prevents identifying that response's exact
cause. The waitlist policy difference is not silently changed to match legacy:
the operator explicitly accepts a waitlist for the approved test. The actual
[Campushallen frontend bundle](https://campushallen.goactivebooking.com/assets/index-DP2StGt5.js)
also sends the same booking payload and supports `allowWaitingList: true`.
Its additional recaptcha payload is conditional on `requireBookingAttestation`;
a fresh `/businessunits` GET showed this flag false for unit 1. No attestation or
access-control bypass was attempted. These checks do not prove that a future
provider business-rule rejection is impossible.

## Production authentication proof

The scheduler was paused and drained before refreshing authentication. Its normal
`AccountGateway.client` / `AccountAuthentication.ensure` validated the current
token. `gateway.reauthenticate` then used the encrypted saved credential for
exactly one real `/auth/login` POST, replacing the token only after successful
identity verification. Both the previous and fresh token passed customer reads;
the fresh token also passed the independent bookings GET. Customer identity was
unchanged and matched legacy. The same HTTP session/header used for these reads
was used to construct the booking request. No token was deliberately corrupted,
no password was displayed, and no booking POST occurred during these checks.
The original live scope and all claims were unchanged.

## Stored selectors and correction

Before correction all three rules used product text `Innebandy 85`, no product ID,
no explicit business-unit ID, generic facility fragment `Campushallen`, and a
preferred time with a window one hour either side. `start_time` was null.
That selected the current intended activities, but could accept a different lone
time within the window or a similarly named product in future data.

After a verified private online backup/restore/off-Pi recovery check, rules 1–3
were pinned to the exact API-confirmed product and requested times:

| Rule / connection | Product / ID | Weekday / fixed local time | Facility / business unit | Type/category | Waitlist/enabled |
| --- | --- | --- | --- | --- | --- |
| 1 / 2 | Innebandy 85 Vallastaden / 1401 | Tuesday / 18:45 | Vallastaden / 1 | group_activity / 40 | true / true |
| 2 / 2 | Innebandy 85 Vallastaden / 1401 | Thursday / 18:45 | Vallastaden / 1 | group_activity / 40 | true / true |
| 3 / 2 | Innebandy 85 Vallastaden / 1401 | Sunday / 12:00 | Vallastaden / 1 | group_activity / 40 | true / true |

Preferred, earliest and latest times equal the fixed time. Business unit 1 is
named Campushallen/LSIF; Vallastaden is identified in the canonical product name
and product ID, not by inventing a different business-unit label. API rooms are
C-salen/C1/C2/C3/C4. All nine selected target IDs/start times were identical before
and after correction. Rule IDs, names, enabled/waitlist preferences, the historical
claim and future target approval were preserved. Safe rule-update events were
recorded. No application authentication fix was required for this correction.

## Candidate and explicit changed instruction

Fresh discovery found no other currently bookable, unclaimed Innebandy occurrence:
22 October (4342667) and 25 October (4410856) are already booked; 29 October
(4342668) opens on 22 October. Tuesday 27 October is the historically claimed
4343840. It had zero regular places, waitlist enabled with eight waiting, and no
remote booking/waitlist entry for Niklas at the audit read. Its API opening was
20 October 18:45 CEST; its start is 27 October 18:45 CET.

The user then explicitly requested: “test next tuesday since we missed to book
that. just join waitlist to see if it works”. This supersedes the earlier no-retry
instruction for that activity only. It does **not** authorize deleting/resetting
the original claim or cancelling anything. The changed interpretation was stated
to the user before implementation or mutation.

A separate durable `booking_reconciliations` ledger makes this change bounded.
A local operator must specify the expected owner, exact activity, active admin,
remote-absence confirmation and finite connection scope. An original claim less
than five minutes old is ineligible. Only one approval can ever be created for
that account/activity; it expires after ten minutes. The original claim timestamp
must still match at consumption. A worker must receive the explicit approval ID
plus `--once --live --confirm-real-bookings`. Normal scheduled workers never pass
that ID and remain blocked by the original claim. Consumption is serialized with
account eligibility and quota, and counts permanently toward total allowance.
Timeouts, verification failures and restarts cannot restore it. Disconnect expires
pending approvals; downgrade refuses to delete consumed approvals. Use only a
schema-compatible worker image after consumption, because older images do not
count the extra ledger when calculating remaining allowance.

The request still runs through the normal BookingEngine: authenticate, reselect,
check target/opening/duplicates/capacity/waitlist, reserve the one-use approval,
record POST start/status, issue the request once and independently verify. The
provider's canonical `type: waitingListBooking` is recorded as verification
WAITLISTED and phase VERIFIED_WAITLISTED. The historical broad result BOOKED
remains compatible; history displays “På reservlistan” for this verified subtype.
No raw provider bodies, credentials, tokens or cookie values enter history/logs.

Execution remains pending deployment and final fresh preflight. The original
future target 4342668 and all three exact rules remain intact.
