# Booking rejection investigation, 21 October 2026

**The booking-count hypothesis is plausible but unproved.** There were four
known provider entries before opening: three regular bookings and one waiting-list
entry. The normal upcoming list subsequently returned three. Go Active recognizes
`TOO_MANY_CONCURRENT_BOOKINGS`, but neither the failed response's error code nor
Niklas's configured numeric maximum was recovered. No retry, claim reset,
cancellation, buffer or selector change is justified by the available evidence.

## Installed legacy versus current worker

Niklas's installed `/home/niklas/booking_bot` source was read without running its
booking command. `booking.py`, `auth.py` and `config.py` are byte-identical to the
repository copies. API differences are the optional saved-auth loader, recording
POST status, and validating discovery response shape; none changes the booking
request. Both HTTPX versions are 0.28.1. The installed browser-storage file is
absent. All three installed rules use the dynamic HTTP path, with no fixed
`start_time`; the browser/fixed-time implementation is not the working comparison.

An intercepted comparison loaded the installed API source with fake credentials,
replaced all network/proxy transports, and exercised login followed by booking.
Method/path/headers/serialized bodies matched exactly. A fake Set-Cookie from
login was retained by both booking requests. This proves construction/session
behavior, not real provider acceptance. Identity equality and actual headers
were previously checked in [the production client audit](booking-client-audit.md).

| Aspect | OLD | NEW | Difference / relevant to 403? |
| --- | --- | --- | --- |
| Login | `/auth/login`, JSON username/password; login username converted to integer customer | Same shared method; verifies customer before encrypted storage | Extra identity check; no demonstrated defect. |
| Session lifetime | One client per rule, across wait/refresh/POST/verification | Fresh account client per operation; shared across preflight/POST/verification; discovery separate | New session may be younger; cookies remain live within each operation. |
| Cookies | Optional browser loader, actual file absent; retains HTTP response cookies | Never loads shared files; retains response cookies | No missing saved cookie demonstrated; intercepted cookie retention equal. |
| Auth header | Local token file; hard-coded Bearer | Encrypted per-account token; same Bearer | Storage differs, real reads and successful waitlist POST prove usable new auth. |
| Refresh | Separate manual login command; no refresh-token exchange | Validates token via customer GET; at most one remembered-password login per operation; no refresh-token exchange | Recovery differs. Forced login after 403 does not establish expiry. |
| Endpoint/type | `/customers/{customer}/bookings/groupactivities`; group activity | Identical | No mismatch demonstrated. |
| Exact payload / ID | Integer `groupActivity`, boolean `allowWaitingList`, optional `recaptchaToken` | Same shared method/serialization | Badminton false in both; web floorball true versus legacy false is explicit policy. |
| Content-Type | application/json | Identical | No difference. |
| Accept | application/json, text/plain, */* | Identical | No difference. |
| Other headers | Swedish Accept-Language, Go Active Referer, mobilityweb request source, bot User-Agent, no Origin | Identical | Host, encoding, connection and length also equal in intercepted requests. |
| Redirects/timeout | Follow redirects; 30 seconds | Identical | No demonstrated difference. |
| Selection | Dynamic selector, refresh same ID | Shared selector plus fixed-time and queued-ID/start checks | More checks; exact rejected target remained 4343863. No configuration cause demonstrated. |
| Product exercised | Retained successful legacy floorball runs | Verified new floorball waitlist; rejected badminton regular request | Different product/booking state: floorball success does not establish badminton entitlement or quota eligibility. |
| Opening | API time, estimated fallback if absent | API time mandatory | Same API opening here; fallback irrelevant. |
| Waiting/buffer | Blocking sleep; last intervals at most 50 ms; final_precision_seconds=2 changes granularity | Async sleep ending at API opening | Neither intentionally waits extra after opening. |
| Work after opening | Refresh target, full bookings GET, capacity/waitlist checks | Auth GET, target refresh, full bookings GET, capacity/waitlist checks, identity/claim transaction and intent records | Explains extra local latency; not evidence of provider rejection cause. |
| Duplicate/count read | Full default bookings GET before wait and immediately before POST; no page/count limit parameter | Full default GET in discovery and immediately before POST; no page/count limit parameter | Neither supplies an invented count or enforces a local maximum of four. |
| POST 401/403 | HTTP exception propagates; no in-function recovery | Records rejection, one bounded reauth, independent GET | Recovery occurs after initial rejection, so cannot explain that rejection. |
| Retry/backoff | No POST retry in installed run_booking | Pre-mutation auth/network backoff; durable no-replay claim | Stronger duplicate protection, no count-based retry. |
| Verification | Independent GET if configured; installed true | Mandatory independent GET, specific BOOKED/WAITLISTED, GET-only auth recovery | Verified waitlist path retained. |

The installed file log contains three retained successes with opening, POST-start
and subsequent verification records:

| Opening, Stockholm time | Legacy POST-start offset | Independently verified in log |
| --- | --- | --- |
| 13 October 18:45 CEST | +0.398 seconds | Yes |
| 15 October 18:45 CEST | +0.330 seconds | Yes |
| 18 October 12:00 CEST | +0.400 seconds | Yes |

These are application log times, not packet-arrival measurements. They show no
historical 5–10 second cushion. Tonight's new-worker offset was +0.953 seconds.
Different activities/account states mean this neither disproves a count race nor
establishes identical backend conditions. No demonstrated new wire difference
currently explains the 403.
The retained legacy logs do not record the number of bookings returned before
those historical POSTs; that number cannot be reconstructed from this file.

## Counts around 19:15

The private 18:52 CEST snapshot preserves provider discovery observations at
18:51:43–46 showing four already-booked entries. A new GET using the official
client's supported `startTimePoint` parameter for 21 October also returns all four:

| Activity | Start in Stockholm | Type |
| --- | --- | --- |
| 4343862, Badminton 70 | 21 October 19:15 CEST | Regular |
| 4342667, Innebandy 85 | 22 October 18:45 CEST | Regular |
| 4410856, Innebandy 85 | 25 October 12:00 CET | Regular |
| 4343840, Innebandy 85 | 27 October 18:45 CET | Waiting list |

At **19:15:00**, 4343862 started. It ended at **20:25 CEST**. The attempted target
4343863 starts **28 October 19:15 CET** and opened 21 October 19:15 CEST.
The retained default bookings read at **19:18:49 CEST** and a new read at
21:12 CEST return **three: two regular, one waiting-list**. A historical-range GET
still returns the past badminton entry. Disappearing from a start-filtered list
does **not** prove when an internal booking-limit rule stops counting it.

The exact list/count at **19:15:00.95** was not saved. History retained the
preceding GET's 200 status, not its count. Four before/three after is a
reconstruction from surrounding observations, not a recovered boundary snapshot.
There is no per-second evidence of a delayed provider transition.

Unknowns: the numeric maximum; whether waiting-list entries count toward it;
whether ongoing activities count until start/end/check-in; and which error code
this POST returned. Separate regular/waiting-list limit codes do not reveal the
installation's configuration. A short retry could also be useless if an ongoing
session counts until its end.

Inspected customer-aware activity/product reads, customer/subscription reads and
business-unit settings expose opening/availability, but no confirmed per-customer
maximum, quota usage, or authoritative overall booking eligibility. List length
is observable, not equivalent to quota usage. `maxFriendsBookTogetherOnGroupActivity`
concerns group booking, not maximum future bookings. A direct `/businessunits/1`
probe returned 404, not proof that no policy endpoint exists elsewhere.

[Campushallen's published rules](https://www.campushallen.se/det-finstilta)
describe the seven-day window, check-in and no-show rules, but the inspected page
does not state a four-booking maximum. No matching published four-booking rule
was found. The earlier waitlist success proves usable new authentication and
mutations, not regular-booking quota eligibility at this later opening.

## Provider reason format and safe changes

The [official frontend bundle](https://campushallen.goactivebooking.com/assets/index-DP2StGt5.js)
function F0 reads **response.data.errorCode**, optional rule, and fieldErrors for
INVALID_INPUT. Its [public Swedish catalog](https://campushallen.goactivebooking.com/locales/sv-SE/translation.6f153b5cd052cb62.json)
contains TOO_MANY_CONCURRENT_BOOKINGS, TOO_MANY_CONCURRENT_WAITING_LISTS,
TOO_EARLY_TO_BOOK, membership restrictions and other distinct reasons. This proves
client support for specific rejection codes, not what tonight's lost response
contained or that every 403 uses this JSON format.

`services/provider_errors.py` retains only exact known public errorCode enums,
mapped to local categories including BOOKING_LIMIT_REACHED, NOT_BOOKABLE_YET,
MEMBERSHIP_RESTRICTION, WAITLIST_RESTRICTION and UNKNOWN_PROVIDER_REJECTION.
HTTP status alone never establishes a business/auth reason. Recognized business
403s get the generic rejection phase rather than the authentication phase.
The historical 401/403 phase label now directs the reader to the provider reason.

Parsing accepts JSON media types, bounds input to 16 KiB, and handles malformed
or deeply nested input. Output consists of bounded local constants. HTML,
unknown codes, arbitrary message/reason/rule/field values, customer IDs, headers,
cookies and secrets are discarded. The safe category/code go into existing phase
history and structured logs with account/rule/activity context. The UI accepts
only exact local messages; untrusted historical text is still suppressed.

AUTH_VALIDATED history additionally saves returned total/regular/waiting-list
counts from the actual pre-POST GET, without the response body. The UI explicitly
says this is an observation, not an eligibility decision. No schema migration,
new request, retry, buffer, selector or authentication policy is introduced.
Independent verification and durable claims remain unchanged.

## Deterministic tests and Thursday

A fake provider at a **19:15** opening reports four entries before POST, replies
403 with the real supported TOO_MANY_CONCURRENT_BOOKINGS code, then reports
three entries with the target absent. Tests assert safe reason/count capture,
one POST, retained claim, and no replay later or after restart. Another test
returns that code but independently confirms a booking: mutation evidence wins
and still forbids replay. These simulate possible conditions, not proof of a
real provider race. Coverage also includes 201 BOOKED/WAITLISTED, auth 401/403,
verification 401, timeout, malformed success and unknown rejection.

**No retry or opening buffer is implemented.** A recognized business code/list
drop does not establish guaranteed non-mutation, quota semantics or safe retry.
Original claims and consumed approval are preserved.

Fresh read-only selection confirms product 1401/unit 1, Innebandy 85 Vallastaden:
Tuesday 18:45 → 4343840 (already waitlisted), Thursday 18:45 → 4342668,
Sunday 12:00 → 4410856 (already booked). All three rules are enabled, with waitlist
allowed. Badminton still selects 4343863 correctly. No rules or legacy configs
were changed.

For **Thursday 22 October 18:45 CEST**, keep the ordinary scheduler/API opening
for **4342668, 29 October 18:45 CET**. Before opening, record actual regular and
waiting counts, exact unbooked/unclaimed target, connection/worker health and
clock; afterward independently verify the outcome. Capture a bounded reason if
rejected. Do not launch a second worker or replay tonight's target. A Thursday
session starting at the same time could create a similar count transition;
success cannot be promised until the provider's actual rule is known. Accepted
Pi power trouble remains a reliability risk, not a demonstrated cause of the 403.

Local validation: **317 tests pass**, including 201 regular/waitlist, known
business-rule rejection, generic auth 401/403, verification 401, uncertain
timeout, four-to-three observations, restart/no-replay and untrusted-body
redaction. Ruff/format passes (104 files), mypy passes (46 modules), 521/521
Swedish messages compile, and the full Chrome desktop/mobile browser smoke
passes against mocked provider calls. One existing Starlette/httpx deprecation
warning remains. ARM64 image and production checks are recorded after release.

## Deployed diagnostic release

At **21:28 CEST on 21 October**, application/source/image
`a95efd7b5cbb8f6d1f100bedb2275137b089b394` is deployed, image ID
`sha256:2c5491c8c90b498c9c1bdb6e95f3f1327015b69755955b22750103c1f56b239c`.
The exact locally built ARM64 image passed production Compose smoke, migrations,
schema agreement, health, isolated backup/restore and restart checks. No build or
test suite was run on the Pi. Explicit production Alembic upgrade/check was a
no-op at schema c8b31d94a207.

Web/worker/cloudflared are healthy, with zero container restarts/OOM, concurrency
3 and both worker live flags. Niklas remains ongoing live. Thursday 4342668 is
unbooked, unclaimed and waiting at the API opening, with 15 places at the fresh
read. Rules, credentials, tokens, original claims/timestamps, consumed approval,
historical attempts, live flags and keys were compared and preserved. Niklas's
legacy timer remains disabled; the friend's timer remains enabled/waiting.

A post-restart check still finds exactly one BOOK_POST_STARTED for 4343863,
with its original 17:15:00.878992 UTC claim. No booking or cancellation was sent
by this investigation; its guarded provider calls were GET-only. A further read
at 21:28 CEST confirms three default upcoming entries, four with the historical
startTimePoint, and the same safe rule matches. The 404 policy probe is retained
as such, not represented as a successful policy read.

Public HTTPS Swedish login passed at 1280px and 390px without overflow, with
Secure/HttpOnly/Strict CSRF cookie and private/no-store response. Targeted retained
log scanning found no known secret-file values or scanned bearer/cookie/invite/
private-key/credential-body patterns; this is not an exhaustive security audit.
Power remains 0x50005, with current undervoltage/throttling; no reboot or router
change occurred.

Private online snapshots before and after deployment:

- campushallen-20261021T192147905083Z-db2594.sqlite3
- campushallen-20261021T192843054106Z-c39a41.sqlite3

Both passed off-Pi integrity/FK checks, matching separate recovery-key verification
and isolated restore on the Mac, preserving both claims and consumed approval.
Private sanitized evidence is outside Git under
`~/Documents/CampushallenBot-Incidents/403-investigation-a95efd7/`.

**The regular booking failure is still unexplained. This release improves
diagnosis; it does not claim to fix a proven provider timing or booking-limit bug.**
