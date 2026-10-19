from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse

from campushallen.database.models import CampushallenAccount
from campushallen.services.account_management import connect_account, disconnect
from campushallen.services.audit import Event, audit
from campushallen.services.credentials import credential_for, forget
from campushallen.services.notifications import Notification, NotificationKind, deliver
from campushallen.services.secrets import ReconnectRequired
from campushallen.services.user_security import check_password
from campushallen.web.dependencies import DB, CurrentUser, Form, owned_account, render
from campushallen.web.i18n import gettext as _
from campushallen.web.presentation import safe_error

router = APIRouter()


@router.get("/accounts")
def accounts(request: Request, db: DB, user: CurrentUser):
    rows = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    return render(request, "accounts.html", user=user, accounts=rows)


@router.post("/accounts")
def add_account(request: Request, db: DB, user: CurrentUser, form: Form):
    name = str(form.get("display_name", "")).strip()
    username = str(form.get("username", "")).strip()
    if not 1 <= len(name) <= 120 or not 1 <= len(username) <= 254:
        raise HTTPException(422, _("Enter an account name and Campushallen username."))
    db.add(CampushallenAccount(user_id=user.id, display_name=name, username=username))
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, _("That account is already added.")) from None
    return RedirectResponse("/accounts", status_code=303)


@router.get("/accounts/{account_id}")
def account_detail(account_id: int, request: Request, db: DB, user: CurrentUser):
    account = owned_account(db, user.id, account_id)
    return render(
        request,
        "account.html",
        user=user,
        account=account,
        remembered=credential_for(db, account.id) is not None,
    )


@router.post("/accounts/{account_id}/connect")
def connect(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    if not request.app.state.limiter.allow(f"connect:{user.id}"):
        raise HTTPException(429, _("Too many connection attempts. Try again in five minutes."))
    password = str(form.get("password", ""))
    if not 1 <= len(password) <= 1024:
        raise HTTPException(422, _("Enter the Campushallen password."))
    try:
        connect_account(
            db,
            request.app.state.gateway,
            request.app.state.credential_vault,
            account,
            password,
            save_password=form.get("remember") == "on",
        )
    except ReconnectRequired:
        return render(
            request,
            "account.html",
            400,
            user=user,
            account=account,
            remembered=credential_for(db, account.id) is not None,
            error=_("Connection failed. Check your credentials or try again later."),
        )
    return RedirectResponse(f"/accounts/{account.id}?notice=connected", status_code=303)


@router.post("/accounts/{account_id}/toggle")
def toggle(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    account.enabled = not account.enabled
    account.auth_lease_until = None
    audit(
        db,
        Event.ACCOUNT_ENABLED if account.enabled else Event.ACCOUNT_DISABLED,
        user_id=user.id,
        actor_id=user.id,
        account_id=account.id,
    )
    db.commit()
    if not account.enabled:
        deliver(
            request.app.state.notifier,
            Notification(NotificationKind.ACCOUNT_DISABLED, user.id, account.id),
        )
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/{account_id}/preview")
def preview(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    # The service only discovers and persists a dry-run; no mutation is exposed here.
    results = request.app.state.engine.preview_account(account.id)
    db.expire_all()
    return render(
        request,
        "account.html",
        user=user,
        account=account,
        results=results,
        remembered=credential_for(db, account.id) is not None,
    )


@router.post("/accounts/{account_id}/credentials/forget")
def forget_password(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    forget(db, account)
    account.auth_lease_until = None
    db.commit()
    return RedirectResponse(f"/accounts/{account.id}?notice=forgotten", status_code=303)


@router.post("/accounts/{account_id}/disconnect")
def disconnect_account(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    if form.get("confirmation") != "DISCONNECT":
        raise HTTPException(
            422, _("Confirm disconnect to remove local tokens and saved credentials.")
        )
    disconnect(db, account)
    return RedirectResponse(f"/accounts/{account.id}?notice=disconnected", status_code=303)


@router.post("/accounts/{account_id}/delete")
def delete_account(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    if not request.app.state.limiter.allow(f"delete-account:{user.id}"):
        raise HTTPException(429, _("Too many attempts. Try again shortly."))
    try:
        check_password(user, str(form.get("password", "")))
        if form.get("confirmation") != "DELETE":
            raise ValueError("Type DELETE to confirm local account removal.")
    except ValueError as exc:
        raise HTTPException(422, safe_error(str(exc))) from None
    audit(db, Event.ACCOUNT_DELETED, user_id=user.id, actor_id=user.id)
    db.delete(account)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)
