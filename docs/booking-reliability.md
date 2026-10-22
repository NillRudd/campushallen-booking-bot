# Authentication and bounded retries, 22 October 2026

This release keeps automatic ongoing live, owner pause/resume, selectors,
waitlist choices and the independent worker. It adds no registration, notification
or cancellation feature. The operator confirmed a provider maximum of **four
concurrent bookings** and explicitly authorized the exact-code retry below.
That confirmation supersedes the earlier uncertainty about the numeric limit;
it does not recover the missing error code from the original badminton incident.

## Cached token, saved password and read recovery

The worker first tries the stored token with a cheap authenticated customer GET.
The provider's response wins over potentially stale local expiry metadata.
A working cached token never spends a password login. An explicitly rejected
token (HTTP 401 or an allowlisted provider authentication code) permits one
saved-password login for the operation. Unknown/generic 403 and malformed
identity responses do not cause speculative password logins.

Only an actual rejected login latches invalid credentials. Temporary transport,
server, malformed-login or identity-validation failures back off for 5 seconds,
doubling to at most 60 seconds across operations. They do not latch reconnection
after an arbitrary failure count. Successful cached validation clears recovered
temporary-error telemetry without requiring another login. Missing/unreadable credentials still need
manual attention. The existing durable lease and compare-and-swap checks retain
protection against concurrent refresh and account changes.
The refresh lease is 180 seconds to allow room for the additional identity-read
attempts; concurrency tests verify it exceeds the former 90-second window.

After login and identity validation, the encrypted replacement token is committed
to SQLite and applied to the original client. Discovery/bookings/verification
GETs can recover a rejection that happens after the initial identity check,
retrying that original read with the new token. The same client cannot attempt
a password twice. A newer token already persisted by another operation is tried
before another login. No refresh-token endpoint is invented.

Account-specific read policy wraps GET calls, not POST, DELETE or login. Each
GET has at most **three attempts**, with waits **0.2 and 0.5 seconds** for transport
failures or HTTP 408/429/500/502/503/504. Auth recovery spends a GET attempt rather
than repeatedly sending the same rejected token. Arbitrary 4xx responses are not
temporary errors. Password-login transport failure is not blindly replayed;
the next bounded account operation may try after its cooldown.

## Before opening

The scheduler performs a GET-only authentication/bookings preflight approximately
**60 seconds before API bookableEarliest**, or immediately if a newly queued
future opening is nearer. It uses the same account authentication flow and cannot
claim or book a target. It briefly uses that account's lock/concurrency permit;
waiting afterward holds no thread, transaction or permit. Shutdown drains an
in-progress preflight. Paused/deleted/disabled connections and rules are checked.

The final run still validates authentication, refreshes selection/opening,
checks duplicates, capacity and waitlist policy, and acquires its durable claim.
Preflight readiness is not a guarantee that the provider/network stays available.

## Booking mutation policy

The initial unique (account, activity) claim is unchanged and persists before any
POST. Each POST has a committed BOOK_POST_STARTED intent with **attempt 1/3,
2/3 or 3/3**. The only automatic repeat condition is:

1. An actual HTTP 4xx JSON response has the exact allowlisted errorCode
   **TOO_MANY_CONCURRENT_BOOKINGS**. A generic 403 does not qualify.
2. A separate bookings GET confirms the target is absent.
3. After a short wait, a fresh bookings GET again finds it absent and returns
   fewer than four entries. Counting all returned entries, including waitlist,
   is deliberately conservative; no undocumented quota endpoint is invented.
4. Selection is refreshed and still identifies the same activity/start, with
   opening reached and capacity/waitlist policy satisfied.
5. A short SQLite writer boundary rechecks active owner/account/rule, live scope,
   identity reservation, original claim timestamp and fewer than three recorded
   POST intents. No network request runs inside that writer boundary.

Count/absence rechecks wait **0.25, 0.5, then 1.0 seconds**, with at most three
checks following a rejection. If the count never drops below four, only the
initial POST is sent. If fresh counts are below four but the provider keeps
returning the exact limit code, at most **three POSTs total** are allowed.
Each retry must qualify again; an unrelated rejection or uncertain result ends
the loop. Pausing, deleting, changing selection or disabling live prevents a
further attempt. Original one-use manual-reconciliation approvals remain
one POST; this new exception does not expand them.

There is no new schema or claim-reset feature. The original executing stack owns
the bounded loop. A crash/restart/competing engine encounters the existing claim
and cannot resume the remaining retry budget. Every attempt, including retry
intent, is recorded before transport. A third attempt is never followed by a
fourth. Historical failed/uncertain claims remain blocked.

## Authentication after POST and unknown outcomes

HTTP 401 or a proven authentication code on the booking POST permits a bounded
login and independent bookings GET, **not another POST**. An HTTP authentication
status alone has not established the provider's non-mutation semantics; the
conditional auth-POST retry mentioned by the operator is therefore not enabled.
If verification discovers a booking, it wins over any rejection label.

When verification itself receives 401, recover authentication and repeat only
the GET. Never repeat an accepted/sent POST because verification authentication
failed. Timeout, connection reset, unknown 5xx, malformed success, redirect or
lost response always use read-only verification. Unknown outcomes retain their
claim and stop. New-worker booking POSTs disable automatic redirects, preventing
HTTPX from silently forwarding a second POST; legacy CLI redirect behavior is
unchanged. There is no cancellation or generic three-POST policy.

## Safe diagnostics and verification

Structured account logs include cached_token_used, token_rejected,
reauth_started/succeeded, auth_preflight_ready/failed, read_retry with attempt,
and booking_post_attempt. Existing phase history retains safe provider enums,
POST attempt n/3, retry readiness, independent verification and final result.
New labels are translated into Swedish. No password, token, cookie, ciphertext,
raw provider body or arbitrary exception text is added to logs/history.

Deterministic mocks cover cached-token success (including stale expiry metadata),
rejected token and successful fallback, invalid password latch, temporary login
outage/backoff/recovery, newer persisted token, restart, isolation, late discovery
401, pre-opening GET-only readiness, three read attempts/backoff, 201 regular and
waitlist success, exact limit rejection/count drop/success, repeated limit up to
three attempts, count stuck at four, pause during retry, generic 403, timeout or
5xx after an eligible first rejection, auth 401 with a real booking, verification
401 without another mutation, and redirect no-replay.

The friend's legacy 401 is motivation only. Its configuration, credentials,
source, service and timer are not modified or restarted by this release.
Pi undervoltage remains an accepted operational risk, not solved by retries.
Local validation: **345 tests pass**, including the existing three-account
concurrency/failure-isolation scenarios. Ruff/format passes (108 files), mypy
passes (47 modules), 524/524 Swedish messages compile, and full Chrome
desktop/mobile browser smoke passes with mocked provider calls. One existing
Starlette/httpx deprecation warning remains. Exact ARM64 image and production
deployment evidence follow after those checks.
