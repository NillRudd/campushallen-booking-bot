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
`bedf9fb5f3b11d32e0a8c6f2757c6b0caeb0da45`. This supersedes the initial
`5c1b08221b247933feaf1d15984bd03997338e33` release. The current commit passed
145 local tests, Ruff, formatting, mypy and the isolated production-image smoke
including migrations, health, backup/restore and restart persistence. The earlier
140-test production image suite and Swedish browser smoke covered the unchanged
application behavior. No branch was pushed.

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
| Containers before reboot | Web, worker and cloudflared all healthy; current memory about 72 / 47 / 39 MiB respectively, below 256 / 256 / 128 MiB limits |
| Tunnel | Official pinned connector image reached healthy readiness; operator confirmed exposed token rotated; replacement supplied through private host file |
| Public HTTPS | Saved route reaches Swedish login with HTTP 200 and valid TLS; authoritative and 1.1.1.1 DNS resolve Cloudflare addresses; Mac system resolver still has an older negative result |
| Public browser smoke | Chrome desktop/mobile login rendered without overflow or page errors, TLS verification enabled; ignored screenshots under diagnostics/production-browser/ |
| Public security | private/no-store, DYNAMIC caching, HSTS, CSP, nosniff, DENY framing, no-referrer; CSRF cookie Secure/HttpOnly/SameSite=strict; cross-origin and missing-CSRF POSTs rejected 403 |
| Debug endpoints | /docs, /redoc and /openapi.json return 404; /health returns 200; unauthenticated /admin redirects |
| HTTP redirect | Plain HTTP currently 400; operator asked to enable Always Use HTTPS at the Cloudflare edge |
| Real admin/invite UI | Operator asked to test private login, dashboard/admin, invitation creation, correct public link, revocation/unavailability; pending |
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
checks passed after the stack reboot. Finish the edge HTTP-to-HTTPS redirect and
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
