from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse

from campushallen.database.models import CampushallenAccount
from campushallen.services.secrets import ReconnectRequired
from campushallen.web.dependencies import DB, CurrentUser, Form, owned_account, render

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
        raise HTTPException(422, "Enter an account name and Campushallen username.")
    db.add(CampushallenAccount(user_id=user.id, display_name=name, username=username))
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, "That account is already added.") from None
    return RedirectResponse("/accounts", status_code=303)


@router.get("/accounts/{account_id}")
def account_detail(account_id: int, request: Request, db: DB, user: CurrentUser):
    account = owned_account(db, user.id, account_id)
    return render(request, "account.html", user=user, account=account)


@router.post("/accounts/{account_id}/connect")
def connect(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    if not request.app.state.limiter.allow(f"connect:{user.id}"):
        raise HTTPException(429, "Too many connection attempts. Try again in five minutes.")
    password = str(form.get("password", ""))
    if not 1 <= len(password) <= 1024:
        raise HTTPException(422, "Enter the Campushallen password.")
    try:
        request.app.state.gateway.connect(account, password)
    except ReconnectRequired as exc:
        account.auth_status = "reconnect_required"
        account.auth_state = None
        db.commit()
        return render(request, "account.html", 400, user=user, account=account, error=str(exc))
    db.commit()
    return RedirectResponse(f"/accounts/{account.id}", status_code=303)


@router.post("/accounts/{account_id}/toggle")
def toggle(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    account.enabled = not account.enabled
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/{account_id}/preview")
def preview(account_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    account = owned_account(db, user.id, account_id)
    # The service only discovers and persists a dry-run; no mutation is exposed here.
    results = request.app.state.engine.preview_account(account.id)
    db.expire_all()
    return render(request, "account.html", user=user, account=account, results=results)
