import time

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import RedirectResponse

from campushallen.database.models import CampushallenAccount, WebSession
from campushallen.services.audit import Event, audit
from campushallen.services.authentication import new_session, token_hash
from campushallen.services.website_login import password_login
from campushallen.web.dependencies import DB, Form, render
from campushallen.web.i18n import gettext as _
from campushallen.web.ingress import client_ip

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", registered=request.query_params.get("registered") == "1")


@router.post("/login")
def login(request: Request, db: DB, form: Form):
    started = time.monotonic()
    # Existing bounded limiter remains a cheap first layer; persistent policy is authoritative.
    source = client_ip(request)
    if not request.app.state.login_limiter.allow(source):
        decision = None
    else:
        decision = password_login(
            db,
            request.app.state.secret,
            str(form.get("username", "")),
            str(form.get("password", "")),
            source,
        )
    # A bounded failure floor reduces the timing difference for nonexistent/locked names
    # without spending Argon2 memory on attempts that have already been blocked.
    if decision is None or not decision.user:
        time.sleep(max(0, 0.3 - (time.monotonic() - started)))
        limited = decision is None or decision.limited
        message = (
            _("Too many failed sign-in attempts. Try again in 30 minutes.")
            if limited
            else _("Invalid username or password.")
        )
        return render(request, "login.html", 429 if limited else 400, error=message)
    user = decision.user
    old = db.get(
        WebSession, token_hash(request.app.state.secret, request.cookies.get("session", ""))
    )
    if old:
        db.delete(old)
    settings = request.app.state.settings
    audit(db, Event.LOGIN_SUCCESS, user_id=user.id, actor_id=user.id)
    try:
        token = new_session(db, user, request.app.state.secret, settings.session_hours)
    except ValueError:
        return render(
            request, "login.html", 400, error=_("Login state changed. Please sign in again.")
        )
    response = RedirectResponse(
        "/onboarding"
        if not db.scalar(
            select(CampushallenAccount.id).where(CampushallenAccount.user_id == user.id).limit(1)
        )
        else "/",
        status_code=303,
    )
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_hours * 3600,
    )
    response.delete_cookie("csrf")
    return response


@router.post("/logout")
def logout(request: Request, db: DB, form: Form):
    session = db.get(
        WebSession, token_hash(request.app.state.secret, request.cookies.get("session", ""))
    )
    if session:
        audit(db, Event.LOGOUT, user_id=session.user_id, actor_id=session.user_id)
        db.delete(session)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    response.delete_cookie("csrf")
    return response
