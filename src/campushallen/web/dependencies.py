import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from campushallen.database.models import BookingRule, CampushallenAccount, User, WebSession, utcnow
from campushallen.services.authentication import token_hash
from campushallen.web.i18n import gettext as _


class LoginRequired(Exception):
    pass


def database(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


DB = Annotated[Session, Depends(database)]


def current_user(request: Request, db: DB) -> User:
    token = request.cookies.get("session", "")
    session = db.get(WebSession, token_hash(request.app.state.secret, token))
    user = db.get(User, session.user_id) if session and session.expires_at > utcnow() else None
    if not user or not user.is_active:
        raise LoginRequired()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def owned_account(db: Session, user_id: int, account_id: int) -> CampushallenAccount:
    account = db.scalar(
        select(CampushallenAccount).where(
            CampushallenAccount.id == account_id, CampushallenAccount.user_id == user_id
        )
    )
    if not account:
        raise HTTPException(404, _("Account not found"))
    return account


def owned_rule(db: Session, user_id: int, rule_id: int) -> BookingRule:
    rule = db.scalar(
        select(BookingRule)
        .join(CampushallenAccount)
        .where(BookingRule.id == rule_id, CampushallenAccount.user_id == user_id)
    )
    if not rule:
        raise HTTPException(404, _("Rule not found"))
    return rule


async def csrf_form(request: Request) -> FormData:
    form = await request.form(max_fields=50, max_files=0)
    cookie = request.cookies.get("csrf", "")
    submitted = str(form.get("csrf_token", ""))
    if not cookie or not secrets.compare_digest(cookie, submitted):
        raise HTTPException(403, _("Invalid CSRF token. Reload the form and try again."))
    try:
        request.app.state.csrf_signer.loads(cookie, max_age=43200)
    except (BadSignature, SignatureExpired):
        raise HTTPException(403, _("Expired CSRF token. Reload the form and try again.")) from None
    return form


Form = Annotated[FormData, Depends(csrf_form)]


def render(request: Request, template: str, status_code: int = 200, **context):
    token = request.cookies.get("csrf")
    try:
        request.app.state.csrf_signer.loads(token or "", max_age=43200)
    except BadSignature:
        token = request.app.state.csrf_signer.dumps(secrets.token_urlsafe(32))
    notices = {
        "connected": _("Campushallen connection updated."),
        "forgotten": _("Saved password removed. Your current token is kept."),
        "disconnected": _("Local authentication data removed. Campushallen bookings were kept."),
        "saved": _("Booking rule saved."),
        "removed": _("Local data removed."),
        "updated": _("Settings updated."),
    }
    context.setdefault("notice", notices.get(request.query_params.get("notice", "")))
    response = request.app.state.templates.TemplateResponse(
        request=request,
        name=template,
        context={"csrf_token": token, **context},
        status_code=status_code,
    )
    response.set_cookie(
        "csrf",
        token,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="strict",
        max_age=43200,
    )
    return response


def admin_user(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(403, _("Administrator access required."))
    return user


AdminUser = Annotated[User, Depends(admin_user)]
