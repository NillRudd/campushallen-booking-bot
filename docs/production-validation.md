# Production preparation verification

Current release status is maintained in [the release gate and operator guide](release-readiness.md).
Earlier incident-freeze and rollout descriptions below are historical snapshots.

**22 October follow-up:** the ordinary scheduler successfully booked regular
Innebandy places for **Niklas and linus99** for 29 October 18:45 CET, with two
201 responses and fresh independent provider confirmation. Four new-worker
POSTs since the previous check all returned 201 and verified at execution; no
new booking 403 was recorded. The friend's separate legacy service failed with
discovery HTTP 401 before opening. See the
[complete read-only diagnostic record](production-diagnostics-2026-10-22.md).

**21 October, 21:28 CEST:** diagnostic release **a95efd7** is deployed, schema
c8b31d94a207. It preserves known provider errorCode values and pre-POST counts
without adding a retry/buffer or changing live policy. The badminton 403 remains
unexplained. **317 tests**, lint/format, mypy (46 modules), Chrome browser smoke
and exact ARM64 production smoke pass. Services and public desktop/mobile HTTPS
login are healthy. Original claims/history, credentials/tokens, rules, keys and
legacy timers are preserved; pre/post backups passed isolated restore, off-Pi
integrity and separate matching-key checks. See the
[behavior/count investigation and release evidence](booking-403-investigation.md).

Updated on 20 October 2026. **First controlled live attempt failed; worker stopped,
claim preserved, investigation blocked on missing API error evidence.** The
operator confirmed private feature acceptance before cutover. Current source is
`c981a44e35d5c14c1d5654d7045343766225d68d`, schema `f2c9a71e6b04`. Niklas's old
timer remains disabled; the friend's remains enabled. See the
[full incident timeline, failure-mode audit and current limits](incident-2026-10-20-first-live-booking.md).
Earlier sections below are historical rollout evidence.

## Source and local checks

The development branches were inspected with status, branch ancestry, graph and
remotes. `feature/web-app` → `feature/pre-deployment-app` → `feature/swedish-ui`
were linear. Local main fast-forwarded from `389687a` to `45cc370` with no history
rewrite. Deployment-specific work is on `deployment/raspberry-pi`; no push was
needed or performed.

| Verification | Result |
| --- | --- |
| Before touching main | 121 tests, Ruff, formatting, mypy, Swedish browser smoke passed |
| Docker before merge | arm64/Python 3.12, 121 tests, web health and migration check passed |
| After fast-forward, before deployment branch | Complete local/browser and Docker suites repeated; 121 tests passed on each |
| Deployment branch suite | 140 tests passed locally on Python 3.14.4 |
| Production image suite | 140 tests passed on Python 3.12; pytest staged only in a disposable test container |
| Final focused backup/login checks | 19 tests passed after explicitly closing backup connections |
| Ruff / format / mypy | Passed; 40 application modules in mypy scope |
| Swedish browser smoke after auth changes | Passed, including admin invites, registration, connection mock, rules, preview, logout |
| Production image | Built for arm64 with pinned base/digests and hashed dependencies; runtime lacks Playwright/pytest |
| Isolated production Compose | Private web + dry-run worker healthy, production secret-file reads, Swedish login and cache headers passed |
| Production backup/restore smoke | Online snapshot, isolated restore and synthetic encryption checks passed |
| Container restart | Temporary database/admin metadata and keys persisted; dry-run worker became healthy again |
| Development Docker after changes | Rebuilt, healthy; Alembic check reports no schema drift |
| Dependency audit | Previous cryptography 48.0.1 advisories resolved by 50.0.2; locked runtime audit reports no known vulnerabilities |
| Cloudflared | Official 2026.10.1 image/digest and readiness command verified locally; no real connector started |
| Frontend catalogs | 416 translated entries, including Swedish lockout and ingress guidance |

No real Campushallen accounts, authentication, booking or cancellation calls were
used in these tests. Tests forbid sockets or mock external calls. Production
Compose smoke used disposable keys/database and never started cloudflared. All
local smoke containers were stopped and temporary production-smoke data removed.
The ordinary development volume was retained. One existing Starlette TestClient
HTTPX deprecation warning remains; no assertions failed.

Cryptography's [official release notes](https://cryptography.io/en/50.0.2/changelog/)
were consulted before upgrading. Existing Fernet/HKDF purpose separation, previous
ciphertext compatibility and reauthentication tests pass. Runtime dependency
scanning is not an OS/container vulnerability assessment or a penetration test.

## Pi deployment and acceptance, 20 October 2026

The current source release and arm64 application image on the Pi are
`b09e2ce0a06a2b793bc2ce4ae25b5b9d0f95411e`, including the committed origin fix
`7f19a0d` and the additional production security tests. This supersedes
`bedf9fb5f3b11d32e0a8c6f2757c6b0caeb0da45`. The current source passed 151 tests,
Ruff, formatting, mypy (40 modules), Swedish browser smoke and isolated
production-image smoke (migrations, health, backup/restore, restart persistence).
No branch was pushed.

| Required fact | Actual evidence / current state |
| --- | --- |
| Host | Debian 13.7 trixie, kernel 6.18.50+rpt-rpi-v8, arm64, UID/GID 1000 |
| Docker | Official repository; CE 29.9.1, Compose 5.6.0; daemon enabled and active after reboot |
| Memory control | Boot flags enabled memory cgroup; harmless 64 MiB container reported memory.max=67108864 |
| Disk | 9.1 GiB free after loading both images; not a load/capacity test |
| Migration | `d8426c9ef017` head; Alembic check reports no new upgrade operations on the current Pi image |
| Data | `~/campushallenbot-data`, mode 0700; independent of both old checkouts |
| Configuration | `~/.config/campushallenbot`, mode 0700; keys and tunnel token mode 0600 |
| Key recovery | Matching encryption key copied privately to Mac `~/Documents/CampushallenBot-Recovery/encryption_key`, directory 0700 / file 0600; synthetic vault check passed |
| Admin | Operator created niklas privately; active admin metadata persists after reboot |
| Current containers | Web, worker and cloudflared healthy after the login-fix deployment; both app containers use the same b09e2ce image |
| Memory observed before earlier reboot | Web, worker and cloudflared all healthy; current memory about 72 / 47 / 39 MiB respectively, below 256 / 256 / 128 MiB limits |
| Tunnel | Official pinned connector image reached healthy readiness; operator confirmed exposed token rotated; replacement supplied through private host file |
| Public HTTPS | Saved route reaches Swedish login with HTTP 200 and valid TLS; authoritative and 1.1.1.1 DNS resolve Cloudflare addresses; Mac system resolver still has an older negative result |
| Public browser smoke | Chrome desktop/mobile login rendered without overflow or page errors, TLS verification enabled; ignored screenshots under diagnostics/production-browser/ |
| Public security | private/no-store, DYNAMIC caching, HSTS, CSP, nosniff, DENY framing, strict-origin; CSRF cookie Secure/HttpOnly/SameSite=strict; cross-origin and missing-CSRF POSTs rejected 403 |
| Debug endpoints | /docs, /redoc and /openapi.json return 404; /health returns 200; unauthenticated /admin redirects |
| HTTP redirect | Operator enabled Always Use HTTPS; public HTTP now returns 301 to the same HTTPS path, which returns 200 |
| Real admin/invite UI | Operator confirmed private login, dashboard, Swedish UI, Admin, invitation creation, correct public URL and revocation/unavailability all passed |
| Backup | Automatic daily run succeeded 20 October 03:38 CEST; fresh online snapshot and isolated restore including niklas metadata/synthetic encryption checks passed |
| Off-Pi database | `~/Documents/CampushallenBot-Backups/campushallen-20261020T131535961565Z-45a79c.sqlite3`, directory 0700 / file 0600; separate from key directory |
| Reboot | Controlled full-stack reboot issued around 15:19 CEST; SSH/Docker returned, all three containers restarted automatically; all three containers became healthy automatically |
| Key persistence | Both production key fingerprints unchanged across full-stack reboot; values never displayed |
| Legacy timers | Both enabled and active after reboot; next run 20 October 18:30 CEST; neither associated service manually triggered |
| Backup timer | Enabled/active after reboot; existing user lingering retained |
| Host ports | Privileged inspection found SSH plus existing mDNS; no host application ports published; tunnel metrics remain private |
| New bookings | Worker command exactly `python -m campushallen.worker`, no live flags; fresh dry-run heartbeat after reboot; no Campushallen accounts imported or connected |
| Hardware | Current/historic undervoltage and throttling remain `0x50005`; operator explicitly authorized continuing |

The [official Raspberry Pi flag table](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)
identifies bits 0 and 2 as current undervoltage and throttling, with historical
bits 16 and 18 also set. Power remains a production reliability risk despite the
operator's override. NTP and Stockholm timezone were checked. No legacy
credentials, auth files or environment files were read, copied or modified.

The tunnel token was accidentally supplied in chat. That value was not installed
or repeated. The operator confirmed rotation in Cloudflare; the replacement file
is private, and the connector subsequently reached healthy readiness. No token
value belongs in this record.

## Pi health-probe correction

The initial worker process persisted dry-run heartbeats, but its container probe
exceeded ten seconds while importing the full application on the throttled Pi.
The current probe uses Python's standard-library SQLite reader with a read-only
connection. It checks the latest non-stopped heartbeat against the same freshness
threshold and requires dry_run=1, without loading keys or initializing the app.
Five regression cases cover fresh/stale, live/stopped workers and a missing DB.
The corrected worker passed its container health check on the actual Pi.

## Remaining production acceptance

Public route, certificate, login rendering, security headers and form rejection
checks passed after the stack reboot. The edge HTTP-to-HTTPS redirect also passed. Finish
private operator browser checks for authenticated cookie/session behavior, admin
and invitation creation/revocation. The operator must enter the website password
privately; admin metadata alone does not prove browser login. Do not claim full
deployment acceptance or live-booking migration complete yet.

During public checks the Mac lost its LAN route to 192.168.1.127. SSH reported
“No route to host,” while repeated public HTTPS checks still returned 200.
The latest successful Pi checks showed all three containers healthy and both
legacy timers active. A private-network reachability issue does not establish an
application outage, but further SSH inspection requires LAN connectivity.

Same-card daily snapshots plus a manually copied Mac snapshot are not an
automated offsite disaster-recovery policy. Current undervoltage, single-host/
microSD failure, and untested concurrent legacy-booking peak memory remain risks.
Real booking remains disabled and the separate future cutover plan is unexecuted.

## Local DNS follow-up

Cloudflare authoritative DNS, 1.1.1.1 and 8.8.8.8 all return the public Cloudflare
IPv4 addresses. The Mac uses the router at 192.168.1.1; this resolver returns no
A answers, although its AAAA lookup succeeds. Normal system lookup and the
operator's Chrome therefore fail; this is not proof of a broken public route.
Curl using a confirmed public address while retaining the real hostname and TLS
verification returns HTTP 301 / HTTPS 200. The operator was guided to select
Cloudflare under Chrome's Secure DNS setting to bypass the failing home resolver.
The operator confirmed the site loads after switching to a mobile network. This
supports the diagnosis of a home-network resolver issue. Private admin login and
invitation acceptance remain pending on the working connection.

## Browser form-origin regression

The operator's first real login returned the Swedish form-origin error before
password verification. A public Chrome reproduction with a random nonexistent
website username confirmed HTTP 403, Origin=null and no Referer. The deployed
Referrer-Policy=no-referrer caused this browser navigation behavior; raw HTTP
origin/CSRF checks and a development-only successful login had missed it.

The prepared correction uses Referrer-Policy=strict-origin: referrer paths remain
private while same-origin form navigation preserves its Origin. The origin and
signed-CSRF checks are unchanged. The browser smoke now asserts the browser's
actual login Origin. All 145 tests, Ruff/format, mypy and the expanded Swedish
browser smoke pass. Deployment and public browser recheck are pending restoration
of Mac-to-Pi LAN connectivity. No password reset is needed for this rejection.

## Continuation review of the origin fix

Resumed on deployment/raspberry-pi with a clean tree at committed fix `7f19a0d`.
The production settings remain canonical public HTTPS, secure cookies, one
allowed hostname and the exact trusted tunnel peer. Origin comparison still
rejects null/arbitrary origins; no proxy trust or CSRF validation was changed.

A controlled Chrome A/B using intercepted synthetic pages confirmed:
no-referrer produced Origin=null with no Referer; strict-origin produced
Origin=https://campushallenbot.se and Referer=https://campushallenbot.se/.
No production POST was sent by this A/B. The
[Fetch Origin algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header)
explicitly applies referrer policy to non-CORS form navigation, including this
null-origin behavior. The prior diagnosis is confirmed, not assumed.
Strict-origin retains only the origin in referrers, including same-origin
requests, so invitation paths stay private. It also suppresses referrers on an
HTTPS-to-HTTP downgrade.

Six additional production cases verify valid-password/valid-CSRF rejection for
null and malicious origins (even with a canonical Referer), missing provenance,
malicious fallback Referer, canonical Referer fallback, and authenticated password
change/session invalidation. The spoofed-forwarding test now checks scheme as
well as client identity. Existing canonical HTTPS login, secure-cookie, Swedish
error and invitation tests remain. All 151 tests, Ruff/format, mypy (40 modules)
and the Swedish browser smoke passed again before deployment. Docker verification
of the final source image and Pi/public acceptance are still pending.

The operator can ping the Pi at 192.168.1.127 from Terminal, whereas Codex's direct
SSH process reports no route. The operator was given a private SSH control-socket
command to authenticate in their own terminal; no password is requested in chat
or persisted by deployment scripts.

## Login-fix deployment, approximately 15:58 CEST on 20 October

All required local checks and the final image's production smoke passed before
deployment. Pi preflight confirmed all previous containers healthy, both legacy
timers enabled/active, and worker command exactly python -m campushallen.worker.
The known 0x50005 power warning persists under the operator's existing override.

A fresh online backup, campushallen-20261020T135307408097Z-c44197.sqlite3, passed
isolated restore with expected niklas admin metadata and synthetic encryption.
It was also copied to the Mac's private CampushallenBot-Backups directory.
The committed arm64 image was built on the Mac and transferred over the user's
terminal-authenticated SSH connection. Source is in a new b09e2ce release directory;
DEPLOYED_COMMIT and the image's revision label match the full commit above.

Only the shared application image reference and source symlink changed. The old
non-secret configuration is retained as
~/.config/campushallenbot/production.env.before-origin-fix. Web and worker were
recreated; cloudflared remained running. No database migration or host reboot was
needed for this header correction. Both production key fingerprints are unchanged.
All three containers are healthy. Worker has a fresh dry-run heartbeat and no
real-booking flags; both legacy live timers remain enabled/active, next observed
run 18:30 CEST today. No legacy unit, configuration or credential was modified.
The schema remains d8426c9ef017 and niklas remains an active admin.

Actual settings are APP_ENV=production, PUBLIC_BASE_URL=https://campushallenbot.se,
SECURE_COOKIES=true. Inspected web/cloudflared log tails show no error/fatal lines;
no secret-bearing log text was displayed. No app ports are published.

Public checks now work through normal Mac DNS. HTTP HEAD returns 301 to the same
HTTPS path. HTTPS HEAD /login returns 405 because this existing route supports
GET; a normal browser GET returns 200 with valid TLS, Swedish UI, private/no-store,
HSTS and Referrer-Policy=strict-origin. A real Chrome form submission using only a
random nonexistent website username sent the canonical public Origin and an
origin-only Referer. It reached the expected invalid-password response (400),
rather than origin rejection (403); no internal hostname was exposed.
No real website password was automated, and no Campushallen call was made.

Stopped for the operator's private login/dashboard/Swedish/Admin check. Invitation
creation/revocation, the operator's Campushallen connection and real UI rules, and
dry-run comparison with the old CLI remain pending and must not be reported as
passed. No real booking/cancellation or live cutover was performed.

## Operator login confirmation

The operator confirmed all four private browser checks passed after deployment:
website login, dashboard, Swedish UI and Admin. No password was supplied to the
agent or used in automated real-user login. The temporary invitation's creation,
public URL and revocation/unavailability check is requested next.

Under the continuation request, subsequent acceptance also includes the operator
connecting their own Campushallen account through the UI, explicitly opting in
to encrypted remembered credentials, creating Innebandy 85 rules for Tuesday and
Thursday 18:45 and Sunday 12:00, previewing, and comparing dry-run targets with
the old CLI's intended selections. These steps are pending. No legacy credentials
will be imported; neither live flags nor old timer state will be changed.

## Invitation acceptance confirmed

The operator confirmed temporary invitation creation, canonical HTTPS public URL,
and revoked-link unavailability all passed through the real website. The invite
link and website password were never supplied to the agent. The operator is now
configuring their own Campushallen connection and three Innebandy 85 rules in the
UI, explicitly enabling encrypted remembered credentials for reauthentication.
The new worker remains dry-run; target comparison is pending account/rule setup.

## First real-account configuration check

The operator reported connection, three rules and preview completed. Read-only
production metadata revealed two owned connections with identical display names
niklas: account 1 is disabled/reconnection-required and owns all three rules;
account 2 is enabled/connected, has a remembered credential row, and owns no
rules. No credential value or ciphertext was read or displayed. No activity
snapshots exist, so target equivalence cannot yet be claimed.

The rule weekdays and preferred times match the request, but fixed start_time is
blank on all three. Waitlist is enabled by the operator and the facility filter
is Campushallen. The comparison is paused before any new API discovery. The
operator was instructed to move the rules through the UI to connected account 2,
set fixed times 18:45 / 18:45 / 12:00 and preview from that account. Identical
account labels in the selector are a confirmed usability limitation. Legacy
timers remain active and all three containers were healthy at this check.

## Activity-discovery deployment preflight, 20 October 2026

Feature tip inspected: `05f4106` on `feature/activity-discovery`; the application
code and tested ARM64 image revision is `39a291f4854e597939147121368bde2a23eda01d`.
The deployment branch is its ancestor at `a803c6b`; main remains `45cc370`.
The working tree was clean, with no history rewrite or push. All required local
checks were repeated: 182 tests, Ruff/format, mypy (42 modules), mocked
desktop/390px browser smoke and isolated ARM64 production smoke passed. The smoke
included migrations/schema agreement, backup/restore and restart persistence.
One existing Starlette TestClient deprecation warning remains.

Production inspection around 17:04 CEST used read-only SQLite connections. User
niklas is active admin ID 1; duplicate connections still block migration:

| Connection | State | Token / remembered credential | Rules | Attempts | Claims | Snapshots |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Disabled, reconnect required; non-email identifier | None / none | 0 | 3 | 0 | 0 |
| 2 | Enabled, connected; email identifier | Decryptable, expires 27 October 14:08 UTC / present | 3 | 13 | 0 | 10 |

Rules 1–3 already belong to connection 2, retaining their IDs. Their configured
preferred times are Tuesday/Thursday 18:45 and Sunday 12:00; fixed times remain
unset and the existing one-hour windows/waitlist policy are unchanged. A guarded
diagnostic allowed only outgoing GET requests and made no database writes or
password-login/token-validation POSTs. The customer GET accepted the existing
token; the bookings GET and existing shared selectors returned:

| Rule | Activity ID | Stockholm start | API bookableEarliest | Free / capacity | Waitlist | Already booked |
| --- | --- | --- | --- | --- | --- | --- |
| 1 Tuesday | 4343839 | 20 October 18:45 +02:00 | 13 October 18:45 +02:00 | 0 / 15 | Enabled, 6 | Yes |
| 2 Thursday | 4342667 | 22 October 18:45 +02:00 | 15 October 18:45 +02:00 | 0 / 15 | Enabled, 11 | Yes |
| 3 Sunday | 4410856 | 25 October 12:00 +01:00 | 18 October 12:00 +02:00 | 0 / 15 | Enabled, 0 | Yes |

All three API products/activities are **Innebandy 85 Vallastaden**, product 1401.
The API business unit is **Campushallen/LSIF** (ID 1), with locations C-salen, C1,
C2, C3 and C4. Vallastaden is part of the activity/product name rather than the
business-unit label. These are checks of the existing deployment's selectors;
post-deployment UI/worker parity has not yet been verified.

Consistent online backup
`campushallen-20261020T150553237050Z-2d4303.sqlite3` passed integrity/foreign-key
checks and isolated restore including niklas admin metadata and synthetic
encryption checks. An additional private copy in the Mac's
`~/Documents/CampushallenBot-Backups/` passed integrity/foreign-key checks. The
existing private off-Pi recovery encryption key matches production; no key value
was displayed. This manual backup retained all existing snapshots.

**Awaiting explicit deletion approval:** retain connection 2 and its existing
rules, reassign only the three historical AUTH_FAILED attempts (IDs 1–3) from
connection 1 to connection 2, then delete obsolete connection 1. Its eight audit
events remain with the deleted connection reference set to NULL by the existing
foreign key. No rules, claims, credentials or tokens need transferring. Recheck
all assumptions transactionally and take a fresh backup before the approved
resolution/migration. No production data changes, source integration, deployment
or migration have occurred in this preflight; Alembic remains `d8426c9ef017`.

Web, worker and cloudflared are healthy on image/source `b09e2ce`. The inspected
worker command is exactly `python -m campushallen.worker`. Both legacy timers
remain active, with next run 20 October 18:30 CEST; neither was triggered or
modified. No reboot has been performed in this continuation. No real booking,
cancellation or live cutover was performed.

## Activity-discovery rollout and production results

The operator explicitly approved the proposed resolution and continued deployment.
The initial reviewed feature tip was
`05f410648383a7185ed1a5013d2cc44ec20b204e`. Deployment first fast-forwarded normally
to `4abf762e064aa39a77f8b6c1d1e64996a7308d1b`. Live acceptance then identified
an API range limitation absent from the mocks: a 21-day schedule request returned
403 while 7-day and 1-day requests succeeded with the same existing token. This
was a period rejection, not an expired token. Browsing now fetches up to three
contiguous weekly periods, keeping the existing short cache and 1,000-row bound.
A regression fixture rejects long periods, and the new test verifies the complete
21-day result, contiguous boundaries across Stockholm's clock change and cache
reuse. No booking/authentication security checks were loosened.

The final application/source/image commit is
**`cf4d4d5852a4e1626f9fc4cec9edbf90c44d3cb6`**. Both feature and deployment
branches were fast-forwarded to this code; main was unchanged and no push was
performed. Before deploying this correction, all **183 tests**, Ruff/format,
mypy (42 modules), mocked desktop/mobile browser smoke and the exact ARM64 image's
isolated production smoke passed. The source archive contains only tracked files;
tracked filename/private-key/token-pattern checks and `git diff --check` passed.
Previous releases/images and private non-secret configuration backups were kept.

| Required fact | Verified result at approximately 17:25 CEST |
| --- | --- |
| Duplicate state before migration | User 1 had disabled connection 1 (0 rules, 3 AUTH_FAILED attempts, no token/credential/claims/snapshots) and connected connection 2 (3 rules, 13 attempts, remembered credential, 0 claims, 10 snapshots). |
| Retained connection | Connection 2, with the existing email login, encrypted credential and token unchanged. |
| Explicit resolution | In one guarded transaction, moved attempt IDs 1–3 to connection 2 and deleted only obsolete connection 1. Rules 1–3 were already on connection 2 and retained unchanged. All eight old audit events remain with account_id NULL. Foreign keys passed; no history rows were deleted. |
| Backup before resolution | Fresh consistent snapshot `campushallen-20261020T150814722006Z-9afe3f.sqlite3`, integrity/foreign-key checks and isolated restore passed. Earlier verified off-Pi pre-resolution snapshot also retained. |
| Backup before migration | Resolved-state snapshot `campushallen-20261020T150935447023Z-9f8cae.sqlite3`, isolated restore and private off-Pi integrity/ownership checks passed. |
| Latest recovery set | Post-migration snapshot `campushallen-20261020T151904914521Z-19eae1.sqlite3`, isolated restore and off-Pi integrity/foreign-key checks passed. Mac backups are under `~/Documents/CampushallenBot-Backups/`; matching private recovery key remains separately under `~/Documents/CampushallenBot-Recovery/`. No key value was displayed. Manual snapshots pruned no older backups. |
| Immediate duplicate guard | With web/worker stopped, no duplicate user_id remained anywhere. The Alembic guard was not bypassed. |
| Migration | `e6b41c9a203d` head; `alembic check` reports no new upgrade operations. The unique user index is present; integrity/foreign keys, row counts, encrypted credential and token preservation passed. |
| Web | Running and healthy on final cf4d4d5 image; one non-root process, no published host ports, no OOM or unexpected restart. |
| Worker | Running and healthy on the same cf4d4d5 image; command exactly `python -m campushallen.worker`; fresh dry_run=1 heartbeat. No real-booking flags. |
| Cloudflared | Running and healthy on the unchanged pinned 2026.10.1 image. Tunnel route/token/configuration were unchanged. |
| Public HTTPS | `/health` and Swedish `/login` return 200 with valid TLS, sv-SE language and private/no-store. Desktop and 390px login rendering/authentication redirects passed without horizontal overflow. No user password or session was used in automation. |
| Hitta pass | Real 21-day discovery found 9 Innebandy occurrences on Tuesday/Thursday/Sunday. Uppercase and lowercase searches returned the same IDs. No new production rule was created. |
| Preview/worker parity | All 10 upcoming plans agreed between the exact preview service and Scheduler.discover_account path on rule/activity ID, product, start, facility/locations and bookableEarliest. Nearest matches for all three rules also agreed with observations written by the actual worker after its final-image startup. |
| Legacy live timers | Both enabled and active/waiting; next run 20 October 18:30 CEST. Neither service was started manually, disabled, edited or removed. Legacy directories were untouched. |
| Persistence / reboot | Both application containers were recreated; connection, rules/history and exact encryption/session key contents persisted. Key files remain 0600. No host reboot was repeated: undervoltage persists and no host/boot configuration changed in this phase. The earlier host reboot remains documented separately. |
| Logs / warnings | Recent web/worker/cloudflared logs had no ERROR/CRITICAL/Traceback lines; no OOM/restart. Cold web health probes initially exceeded five seconds before recovering automatically. Known current/historical power flags remain 0x50005 under the operator's prior override. One local Starlette TestClient deprecation warning remains. |
| Final private browser acceptance | Requested from the operator: login, Översikt, single Mitt Campushallen-konto/email label, 21-day Hitta pass search, existing three rules and each Matchande kommande pass. Pending; authenticated UI rendering has not been claimed as automated acceptance. |

Nearest production matches from the final preview/worker check:

| Rule | Activity | Stockholm start | API opening | Available / capacity | Waiting list | Already booked |
| --- | --- | --- | --- | --- | --- | --- |
| Tuesday, rule 1 | 4343839 | 20 October 18:45 +02:00 | 13 October 18:45 +02:00 | 0 / 15 | 5 | Yes |
| Thursday, rule 2 | 4342667 | 22 October 18:45 +02:00 | 15 October 18:45 +02:00 | 0 / 15 | 11 | Yes |
| Sunday, rule 3 | 4410856 | 25 October 12:00 +01:00 | 18 October 12:00 +02:00 | 0 / 15 | 0 | Yes |

All are product **Innebandy 85 Vallastaden**, product ID 1401, business unit
Campushallen/LSIF (1), rooms C-salen/C1–C4. Availability/waitlist counts are observed
values and can change. The final diagnostic made 29 GET requests, no outgoing
non-GET requests, and recorded only local preview/discovery observations. It did
not run a booking operation. Durable live-claim count remained zero.

**NO REAL BOOKING WAS PERFORMED. NO CANCELLATION WAS PERFORMED.
LIVE CUTOVER HAS NOT OCCURRED.** The new worker remains dry-run; legacy bots
remain the only live booking mechanism. No future live cutover is authorized by
this deployment approval.
