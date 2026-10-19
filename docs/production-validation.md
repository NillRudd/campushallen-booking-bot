# Production preparation verification

Recorded on 19 October 2026. **Pi installation is in progress; public acceptance remains incomplete.**

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

## Pi deployment progress and remaining acceptance

The source release and arm64 image installed on the Pi are both
`5c1b08221b247933feaf1d15984bd03997338e33`. Before transfer, the complete local
140-test suite, Ruff, formatting, mypy, Swedish browser smoke and isolated
production-image smoke passed again for this exact commit. No branch was pushed.

| Required fact | Actual evidence / current state |
| --- | --- |
| Host | Debian 13.7 trixie, kernel 6.18.50+rpt-rpi-v8, arm64, UID/GID 1000 |
| Docker | Official repository; CE 29.9.1, Compose 5.6.0; daemon enabled at boot |
| Memory control | Boot flags enabled memory cgroup; a harmless 64 MiB container reported memory.max=67108864 |
| Resources after installation | 9.3 GiB disk free; 905 MiB RAM total, 603 MiB available at observation; not a load test |
| Migration | `d8426c9ef017` head; Alembic check reports no new upgrade operations |
| Data | `~/campushallenbot-data`; independent of both old checkouts |
| Configuration | `~/.config/campushallenbot` mode 0700; secret_key and encryption_key mode 0600 |
| Key recovery | Matching encryption key copied privately to Mac `~/Documents/CampushallenBot-Recovery/encryption_key`, directory 0700 / file 0600; synthetic vault check passed |
| Admin | Operator created niklas privately; database confirms active admin |
| Containers | Web healthy; worker running with a persisted dry-run heartbeat; no host ports published |
| Tunnel | Official pinned image downloaded; replacement token and connector startup pending |
| Public HTTPS/admin/invites | Pending; no public acceptance claimed |
| Backup | Online snapshots created; isolated restore of `campushallen-20261019T200238906746Z-628576.sqlite3` confirms niklas admin metadata and synthetic encryption checks; daily website timer enabled, next 20 October 03:38 CEST |
| Reboot | Prerequisite memory-cgroup reboot passed; final full-stack acceptance reboot pending |
| Legacy timers | Both enabled and active/waiting after reboot; next observed run 20 October 18:30 CEST; associated services inactive |
| Host ports | Privileged pre-install inspection found SSH plus existing mDNS; no application ports published by Compose |
| New bookings | Worker configuration has neither real-booking flag; no Campushallen accounts imported or connected |
| Hardware | Current and historic undervoltage/throttling `0x50005`; operator explicitly authorized continuing |

The [official Raspberry Pi flag table](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)
identifies bits 0 and 2 as current undervoltage and throttling, with historical
bits 16 and 18 also set. Power remains a production reliability risk after the
operator's override. NTP and Stockholm timezone were checked. Existing user
lingering is enabled. No legacy credentials, auth files or environment files were
read, copied or modified. No old booking service was manually triggered.

The tunnel token was accidentally supplied in chat. It was not installed or
repeated; the operator was instructed to rotate it in Cloudflare and enter the
replacement through the host-side hidden prompt. Replacement confirmation remains
pending. No token value belongs in this record.

## Pi health-probe correction

The initial worker process persisted healthy dry-run heartbeats, but its container
probe exceeded ten seconds while importing the full application on the throttled
Pi. The probe now uses Python's standard-library SQLite reader with a read-only
connection, checks the latest non-stopped heartbeat against the same freshness
threshold and requires dry_run=1. It needs no keys or application initialization.
Regression tests cover fresh/stale, live/stopped workers and missing databases.
The replacement image must pass local checks and Pi health acceptance before the
stack is considered healthy; initial heartbeat alone is not that acceptance.
