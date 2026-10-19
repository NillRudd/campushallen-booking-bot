from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import RedirectResponse

from campushallen.database.models import AuditEvent, WebSession
from campushallen.services.user_security import change_password, delete_user, logout_all
from campushallen.web.dependencies import DB, CurrentUser, Form, render
from campushallen.web.i18n import gettext as _
from campushallen.web.presentation import safe_error

router = APIRouter()


def logged_out() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    response.delete_cookie("csrf")
    return response


@router.get("/settings")
def settings_page(request: Request, db: DB, user: CurrentUser):
    sessions = db.scalars(select(WebSession).where(WebSession.user_id == user.id)).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.user_id == user.id)
        .order_by(AuditEvent.id.desc())
        .limit(100)
    ).all()
    return render(request, "settings.html", user=user, sessions=sessions, events=events)


@router.post("/settings/password")
def password_change(request: Request, db: DB, user: CurrentUser, form: Form):
    if not request.app.state.limiter.allow(f"password:{user.id}"):
        raise HTTPException(429, _("Too many attempts. Try again shortly."))
    try:
        change_password(
            db,
            user,
            str(form.get("current_password", "")),
            str(form.get("new_password", "")),
            str(form.get("confirmation", "")),
        )
    except ValueError as exc:
        raise HTTPException(422, safe_error(str(exc))) from None
    return logged_out()


@router.post("/settings/sessions/logout-all")
def all_sessions(request: Request, db: DB, user: CurrentUser, form: Form):
    logout_all(db, user)
    return logged_out()


@router.post("/settings/delete")
def remove_user(request: Request, db: DB, user: CurrentUser, form: Form):
    if not request.app.state.limiter.allow(f"delete-user:{user.id}"):
        raise HTTPException(429, _("Too many attempts. Try again shortly."))
    try:
        delete_user(db, user, str(form.get("password", "")), str(form.get("confirmation", "")))
    except ValueError as exc:
        raise HTTPException(422, safe_error(str(exc))) from None
    return logged_out()
