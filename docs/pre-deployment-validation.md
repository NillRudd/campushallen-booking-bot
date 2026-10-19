# Product/security phase verification

Verified locally on 19 October 2026, on `feature/pre-deployment-app` branched from
`feature/web-app`. No push, public exposure, or production deployment was performed.

| Check | Result |
| --- | --- |
| Previous-phase baseline | 55 tests, Ruff and mypy passed before changes |
| Full suite, local Python 3.14.4 | 94 tests passed |
| Full suite, Docker Python 3.12 | 94 tests passed |
| `make lint` | Ruff checks and formatting passed |
| `make typecheck` | mypy passed for 35 application modules |
| `alembic check` | No schema drift |
| Previous-schema migration test | Upgrade, downgrade, re-upgrade preserve users, token ciphertext and sessions |
| Docker build/start | Built successfully, healthcheck passed; UID 10001 |
| HTTP probes in container | `/health` returned `{"status":"ok"}`; `/login` returned 200 |
| Default worker | `campushallen-worker --once` exited successfully in dry-run mode and persisted heartbeat |
| Encrypted credential persistence | Saved ciphertext and development key in an isolated named volume, decrypted successfully in a fresh second container; test volume removed |
| Browser smoke | Admin invite creation, registration, onboarding, mocked connection with credential opt-in, basic rule, preview, dashboard, history filters, forget credential, logout-all passed |
| Browser layout | Desktop and 390px mobile inspected; no horizontal page overflow or JavaScript errors |
| Compatibility diff | Legacy API/CLI booking modules, original tests, systemd files and installer unchanged |
| `git diff --check` | Passed |

## Automated coverage

New tests cover credential opt-in/discard, ciphertext storage and replacement,
forgetting, correct/wrong keys, account/purpose/version binding, legacy token
compatibility, token validation, automatic login, permanent failure latch,
backoff and bounded retries, concurrent reauthentication lease, disabling during
reauthentication, and worker account-failure isolation.

Invitation tests cover hashes, one-time creation output, registration validation,
expiry/revocation/consumption, atomic concurrent use, username collision rollback,
CLI batch generation/list/revoke, admin bootstrap, authorization and rate limits.
Security tests cover website password change and long passphrases, logout-all,
CSRF, ownership on new account actions, secret-free HTML/logs, invitation log
redaction, bounded declared/chunked request bodies, immediate expired-token status,
local disconnect/delete, cascade policy and last-admin protection.

Operational tests cover healthy/stale/stopped heartbeat, discovery snapshots,
API-derived next-action time, completed-target status, notification failure
isolation, dashboard states, history filters and safe rule prefill. The original
55 tests remain passing alongside these additions.

## Safety and reproducibility

Pytest fixtures prohibit real socket connections. API behavior is faked or uses
HTTPX MockTransport. The browser smoke also uses MockTransport and explicitly
rejects any non-GET API call except mocked login. No real Campushallen credentials,
booking, or cancellation calls were used. Browser data uses a temporary database
and random website logins; screenshots contain only fake test account/activity
information and are written under ignored `diagnostics/browser-smoke/`.

Run locally after editable installation:

```sh
make test lint typecheck
alembic check
python -m playwright install chromium
make browser-smoke
# Or CAMPUSHALLEN_BROWSER_CHANNEL=chrome make browser-smoke
```

Docker smoke used the development Compose configuration, whose host binding
remains `127.0.0.1:8000`. Test dependencies and test files were staged inside the
throwaway container, not added to the runtime image. Test containers were stopped
after verification; the existing development named volume was retained.

One existing Starlette TestClient deprecation warning concerns its HTTPX adapter;
it does not affect passing assertions. These checks are development verification,
not a penetration test or approval to deploy. See the
[pre-deployment checklist](pre-deployment-checklist.md) for the next phase.
