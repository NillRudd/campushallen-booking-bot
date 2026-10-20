import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from campushallen.database.session import make_engine, session_factory
from campushallen.services.account_auth import AccountAuthentication
from campushallen.services.accounts import AccountGateway
from campushallen.services.authentication import LoginLimiter
from campushallen.services.discovery import ActivityDiscovery
from campushallen.services.engine import BookingEngine
from campushallen.services.logging import InviteAccessFilter
from campushallen.services.notifications import NoopNotifier
from campushallen.services.secrets import CredentialVault, TokenVault, runtime_key
from campushallen.settings import Settings
from campushallen.web import i18n, presentation
from campushallen.web.dependencies import LoginRequired, render
from campushallen.web.i18n import gettext as _
from campushallen.web.ingress import TrustedIngress
from campushallen.web.middleware import FormBodyLimit
from campushallen.web.presentation import connection_status
from campushallen.web.routes import (
    accounts,
    activities,
    admin,
    auth,
    invites,
    pages,
    rules,
    security,
)

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
    if settings.app_env == "production":
        from urllib.parse import urlsplit

        if not settings.secure_cookies or not settings.public_base_url.startswith("https://"):
            raise ValueError("Production requires HTTPS PUBLIC_BASE_URL and SECURE_COOKIES=true")
        if settings.allowed_hosts != [urlsplit(settings.public_base_url).hostname]:
            raise ValueError("Production ALLOWED_HOSTS must contain only the public hostname")
    app.add_middleware(FormBodyLimit)
    app.add_middleware(TrustedIngress, settings=settings)
    if settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts, www_redirect=False
        )
    app.state.settings = settings
    app.state.sessions = session_factory(engine)
    app.state.secret = runtime_key(settings, "secret_key")
    app.state.csrf_signer = URLSafeTimedSerializer(app.state.secret, salt="browser-csrf")
    app.state.selection_signer = URLSafeTimedSerializer(app.state.secret, salt="activity-selection")
    app.state.limiter = LoginLimiter()
    app.state.login_limiter = LoginLimiter(limit=20, window=900)
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
    app.state.discovery = ActivityDiscovery(app.state.gateway, settings.campushallen_timezone)
    app.state.engine = BookingEngine(app.state.sessions, app.state.gateway, app.state.notifier)
    app.state.templates = Jinja2Templates(directory=str(ROOT / "templates"))
    env = app.state.templates.env
    env.add_extension("jinja2.ext.i18n")
    env.install_gettext_translations(i18n.translations(settings.default_locale), newstyle=True)
    env.filters["localtime"] = lambda value: i18n.local_datetime(
        value, settings.campushallen_timezone, settings.default_locale
    )
    env.filters["clock"] = lambda value: i18n.clock(value, settings.default_locale)
    env.filters["phase_label"] = presentation.phase_label
    env.filters["connection_status"] = lambda account: connection_status(account, tokens)
    for name in (
        "friendly_result",
        "verification_label",
        "invite_status",
        "reason",
        "status_label",
        "event_label",
        "worker_label",
    ):
        env.filters[name] = getattr(presentation, name)
    env.globals["weekdays"] = i18n.weekdays(settings.default_locale)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        locale_token = i18n.LOCALE.set(settings.default_locale)
        try:
            response = await call_next(request)
        finally:
            i18n.LOCALE.reset(locale_token)
        response.headers["Content-Language"] = settings.default_locale.replace("_", "-")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Keep bearer paths private while preserving Origin on browser form navigation.
        response.headers["Referrer-Policy"] = "strict-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        response.headers["Cache-Control"] = "private, no-store"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(LoginRequired)
    async def need_login(request, exc):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        messages = {
            404: _("Page not found."),
            405: _("This action is not allowed."),
        }
        # Our route errors are already translated. Framework errors use safe local copy.
        detail = (
            messages.get(exc.status_code)
            if str(exc.detail) in ("Not Found", "Method Not Allowed")
            else str(exc.detail)
        )
        if exc.status_code == 400:
            detail = _("Invalid request. Check the form and try again.")
        return render(request, "error.html", exc.status_code, error=detail)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return render(
            request, "error.html", 422, error=_("Invalid request. Check the form and try again.")
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return render(
            request, "error.html", 500, error=_("Something went wrong. Please try again later.")
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
        activities.router,
        rules.router,
        invites.router,
        admin.router,
        security.router,
    ):
        app.include_router(router)
    return app
