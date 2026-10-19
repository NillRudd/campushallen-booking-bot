import logging
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from campushallen.database.session import make_engine, session_factory
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway
from campushallen.services.authentication import LoginLimiter
from campushallen.services.engine import BookingEngine
from campushallen.services.invitations import invite_status
from campushallen.services.logging import InviteAccessFilter
from campushallen.services.notifications import NoopNotifier
from campushallen.services.secrets import CredentialVault, TokenVault, runtime_key
from campushallen.settings import Settings
from campushallen.web.dependencies import LoginRequired, render
from campushallen.web.middleware import FormBodyLimit
from campushallen.web.presentation import connection_status, friendly_result, verification_label
from campushallen.web.routes import accounts, admin, auth, invites, pages, rules, security

ROOT = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, InviteAccessFilter) for f in access_log.filters):
        access_log.addFilter(InviteAccessFilter())
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(
        title="Campushallen Booker",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(FormBodyLimit)
    app.state.settings = settings
    app.state.sessions = session_factory(engine)
    app.state.secret = runtime_key(settings, "secret_key")
    app.state.csrf_signer = URLSafeTimedSerializer(app.state.secret, salt="browser-csrf")
    app.state.limiter = LoginLimiter()
    master_key = runtime_key(settings, "encryption_key")
    tokens = TokenVault(master_key)
    app.state.credential_vault = CredentialVault(master_key)
    app.state.notifier = NoopNotifier()
    app.state.gateway = AccountGateway(settings, tokens)
    app.state.gateway.auth_service = AccountAuthentication(
        app.state.sessions,
        app.state.gateway,
        tokens,
        app.state.credential_vault,
        app.state.notifier,
    )
    app.state.engine = BookingEngine(app.state.sessions, app.state.gateway, app.state.notifier)
    app.state.templates = Jinja2Templates(directory=str(ROOT / "templates"))
    tz = ZoneInfo(settings.campushallen_timezone)
    app.state.templates.env.filters["localtime"] = lambda value: (
        value.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z") if value else "—"
    )
    app.state.templates.env.filters["clock"] = lambda value: (
        value.strftime("%H:%M") if hasattr(value, "strftime") else str(value or "")
    )
    app.state.templates.env.filters["connection_status"] = lambda account: connection_status(
        account, tokens
    )
    app.state.templates.env.filters["friendly_result"] = friendly_result
    app.state.templates.env.filters["verification_label"] = verification_label
    app.state.templates.env.filters["invite_status"] = invite_status
    app.state.templates.env.globals["weekdays"] = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(LoginRequired)
    async def need_login(request, exc):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return render(request, "error.html", exc.status_code, error=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return render(
            request, "error.html", 422, error="Invalid request. Check the form and try again."
        )

    @app.get("/health")
    def health():
        try:
            with engine.connect() as connection:
                # Probe migrated tables, not only SQLite connectivity.
                connection.execute(text("SELECT id FROM users LIMIT 1"))
            return JSONResponse({"status": "ok"})
        except SQLAlchemyError:
            return JSONResponse({"status": "database_unavailable"}, status_code=503)

    for router in (
        auth.router,
        pages.router,
        accounts.router,
        rules.router,
        invites.router,
        admin.router,
        security.router,
    ):
        app.include_router(router)
    return app
