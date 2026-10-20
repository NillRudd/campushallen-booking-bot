# Production preparation verification

Updated on 20 October 2026. **Pi stack and public HTTPS running; browser acceptance remains incomplete.**

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
