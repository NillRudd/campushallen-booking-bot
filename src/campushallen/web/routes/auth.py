from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import RedirectResponse

from campushallen.database.models import CampushallenAccount, WebSession
from campushallen.services.audit import Event, audit
from campushallen.services.authentication import authenticate, new_session, token_hash
from campushallen.web.dependencies import DB, Form, render
from campushallen.web.i18n import gettext as _

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", registered=request.query_params.get("registered") == "1")


@router.post("/login")
def login(request: Request, db: DB, form: Form):
    key = request.client.host if request.client else "local"
    if not request.app.state.limiter.allow(key):
        raise HTTPException(429, _("Too many login attempts. Try again in five minutes."))
    password = str(form.get("password", ""))
    if len(password) > 4096:
        return render(request, "login.html", 400, error=_("Invalid username or password."))
    user = authenticate(db, str(form.get("username", "")), password)
    if not user:
        audit(db, Event.LOGIN_FAILED)
        db.commit()
        return render(request, "login.html", 400, error=_("Invalid username or password."))
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
