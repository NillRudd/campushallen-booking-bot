# Development verification

Verified on 19 October 2026 on `feature/web-app`.

| Check | Result |
| --- | --- |
| Existing test suite before changes | 24 passed |
| Complete suite, local Python 3.14.4 | 55 passed |
| Complete suite, Docker Python 3.12 | 55 passed |
| `make lint` | Ruff checks and formatting passed |
| `make typecheck` | mypy passed for all 22 new application modules |
| `alembic check` | No schema drift |
| Migration tests | Upgrade, downgrade, re-upgrade, constraints, UTC round trip passed |
| `docker compose build web` | Built successfully |
| `docker compose up -d --wait web` | Container healthy; UID 10001 |
| Container HTTP checks | `/health` returned `{"status":"ok"}`; `/login` returned 200 |
| Container `campushallen-worker --once` | Exited successfully with empty development data |
| Browser smoke, headless Chrome | Login, account creation, rule creation, dashboard, disconnected preview passed |
| Responsive check | Desktop and 390px mobile layout inspected; no horizontal page overflow |
| Browser errors | No JavaScript errors |
| `git diff --check` | Passed |

Tests block real socket connections; external API behavior uses fake gateways
and HTTPX MockTransport. No real Campushallen credentials, bookings, or
cancellations were used. Browser smoke tests used a temporary database and a
random application login, then removed that temporary data. Docker smoke tests
used an empty development database. Smoke-test containers were stopped afterwards;
the named development data volume was retained.

The current Starlette TestClient emits one deprecation warning about its HTTPX
adapter. This does not affect the passing assertions or runtime web application.
No production Raspberry Pi or systemd configuration was accessed or changed.
The existing CLI modules and original 24 tests are unchanged, apart from the
HTTP client's optional switch to bypass shared CLI authentication state.
