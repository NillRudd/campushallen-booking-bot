# Scheduled badminton opening, 21 October 2026

**The normal worker ran on time, but Campushallen rejected the booking POST with
HTTP 403. Two independent bookings reads confirmed NOT_BOOKED.** One POST phase
was recorded, the claim is retained, and there was no second booking request or
cancellation. The provider-specific rejection reason is not established.

See [the follow-up investigation](booking-403-investigation.md) for installed
legacy timing, reconstructed booking counts, the unproved booking-limit
hypothesis and safe provider-error capture. No retry or opening buffer is justified.

## Intended activity and actual sequence

Niklas connection 2, rule 4: **4343863, Badminton 70 Vallastaden**, product 3678,
business unit 1. The session starts **28 October 19:15 CET**; API opening is
**21 October 19:15 CEST**. The user enabled the rule and explicitly authorized
ongoing automatic booking. Its waitlist preference is false and was unchanged.

At the five-minute preflight, the worker's fresh observation showed 26 regular
places, no existing booking and no claim. Identity reservation, ongoing live,
authentication, enabled rule and synchronized clock all passed. Web, worker and
tunnel were healthy. Production remained unchanged through the opening. The
worker used application ad8e3fb and the ordinary scheduler/engine/gateway; no
manual worker invocation or raw booking call was used.

| Time, CEST | History | Evidence |
| --- | --- | --- |
| 19:15:00.878992 | durable claim | Connection 2 / activity 4343863, committed before POST |
| 19:15:00.907448 | 83 AUTH_VALIDATED | Authenticated bookings read HTTP 200 |
| 19:15:00.953488 | 84 BOOK_POST_STARTED | One recorded booking request start |
| 19:15:01.161108 | 85 BOOK_POST_AUTH_REJECTED | Booking POST HTTP 403 |
| 19:15:01.668786 | REAUTH_SUCCESS audit | One automatic login succeeded; no booking POST replay |
| 19:15:01.798556 | 86 VERIFY_FAILED | Independent bookings GET HTTP 200, NOT_BOOKED |
| 19:15:26.949449 | separate read-only monitor | Fresh client/customer validation and bookings GET both 200, NOT_BOOKED |

The final broad local result is VERIFICATION_FAILED, with precise verification
NOT_BOOKED. Despite the phase label BOOK_POST_AUTH_REJECTED, HTTP 403 alone does
not prove a bad/expired password or token. Pre-book reads succeeded, a password
login succeeded, and subsequent authenticated reads succeeded. The exact reason
cannot be reconstructed because no raw provider response or provider-specific
error payload was retained. The original 20 October incident remains separate.

## Read-only investigation and disposition

The [official frontend bundle](https://campushallen.goactivebooking.com/assets/index-DP2StGt5.js) identifies the customer-aware GET paths for activity,
product and subscriptions. These reads all returned 200. With and without the
customer parameter the target opening remained 17:15 UTC, and 26 places were
still available afterward. Server Date headers matched the local UTC second.
Customer flags showed accepted booking terms, membership present and suspended
false. The product does not require participants to pay; displayed price is null.
Both subscription date ranges/debited-through dates covered the target, neither
showed a freeze, and unit 1 still has requireBookingAttestation false. No obvious
expiry, payment, attestation or clock issue was demonstrated. These checks do
not prove every private provider booking/entitlement rule is satisfied and do
not identify the 403 cause. No restriction was bypassed and no terms/payment
acceptance was submitted.

The claim for 4343863 remains at its original timestamp. It is not reset or
replayed. Historical claim 4343840 and consumed manual approval 1 are also
unchanged. Other legitimate future activities remain eligible under the user's
ongoing policy, including floorball 4342668 opening 22 October at 18:45 CEST.
The old Niklas timer is disabled, and the friend's legacy timer is untouched.
No booking was cancelled.

A separate read-only Pi monitor persisted through the opening independently of
the Mac. Sanitized evidence is private under
`~/Documents/CampushallenBot-Incidents/automatic-live-ad8e3fb/`; no password,
token, cookie, encryption key or raw API response is included in this document.
Post-attempt snapshot `campushallen-20261021T171658006439Z-9364a0.sqlite3` passed
off-Pi integrity, matching separate recovery-key verification and an isolated
restore test on the Mac. It preserves both old and new claim states. Do not
restore a pre-attempt snapshot over the new claim.

After the opening and preserved evidence, b29e631 was deployed to repair a
separate pause/resume edge case: waiting tasks that exited while a user paused
could otherwise stay completed in memory after resume. Only pre-mutation paused
or pre-book-auth-failed jobs become resumable; persisted claimed/uncertain results
still pass the durable deduplication gate and never replay a booking POST.
Account pause, rule pause, reconnect and no-replay regressions pass. **281 tests**,
Ruff/format, mypy and the exact ARM64 production image smoke pass; the automatic
live/owner-controls browser flow also passed. Services are healthy, concurrency
is 3, ongoing flags are preserved and the failed target remains claimed.

**The automatic-live and opening-time execution changes are deployed. This
regular-place booking test did not succeed and must not be described as a
verified booking.** The earlier real waitlist success remains valid. Current
Pi undervoltage/throttling (0x50005) and unverified router forwarding remain
operational limits; the exact provider 403 cause is additionally unresolved.
