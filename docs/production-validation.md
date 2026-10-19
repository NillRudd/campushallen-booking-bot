# Production preparation verification

Recorded on 19 October 2026. **No production deployment has occurred.**

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

## Production acceptance still outstanding

| Required fact | Actual evidence / current state |
| --- | --- |
| Deployed Git commit | None; only local source/images have been prepared |
| Pi preflight and resources | Unknown; SSH to the specified IPv4 and local hostname returns “No route to host” |
| Legacy timer health | Not remotely verified; no timer/unit/config/auth file was modified or triggered |
| Production containers / tunnel | Not installed or started by this task |
| Public URL | Mac HTTPS probe could not resolve campushallenbot.se; no DNS change was made |
| Persistent production directory | Planned `~/campushallenbot-data`; not created on the Pi |
| Production secret files | Planned `~/.config/campushallenbot/{secret_key,encryption_key,cloudflare_tunnel_token}`; not provisioned |
| Production admin | Not created; user must enter its website password interactively |
| Production backup / off-Pi key | Not created; local temporary smoke is not a production recovery copy |
| Pi reboot / recovery | Not attempted; still requires resource/timer checks and a completed initial stack |
| Real website admin/invite smoke | Pending authenticated public application |
| Hardware warnings | Undervoltage, disk space, memory, time sync and exposed host ports cannot yet be assessed |
| New live bookings | Not enabled; active production worker command has neither real-booking flag |
| Future cutover | Documented separately; no cutover action authorized or executed in this phase |

Restore LAN/SSH access first. Then perform the Pi preflight before any install,
provision production keys, bootstrap the admin, have the user supply the tunnel
token privately, publish the apex hostname and complete all real acceptance and
reboot checks. Do not treat this local verification record as production signoff.
