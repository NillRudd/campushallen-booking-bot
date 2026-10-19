from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import FormData
from starlette.responses import RedirectResponse

from campushallen.database.models import BookingRule, CampushallenAccount
from campushallen.services.rules import RuleInput
from campushallen.web.dependencies import DB, CurrentUser, Form, owned_account, owned_rule, render

router = APIRouter()


def parse_rule(form: FormData) -> RuleInput:
    fields = {
        name: str(form.get(name, ""))
        for name in (
            "name",
            "activity",
            "product_name",
            "weekday",
            "booking_type",
            "web_category_id",
            "start_time",
            "preferred_start_time",
            "earliest_allowed_time",
            "latest_allowed_time",
        )
    }
    for name in ("product_name", "start_time", "preferred_start_time"):
        if not fields[name]:
            fields.pop(name)
    values: dict[str, Any] = dict(fields)
    values["facility_contains"] = [
        v.strip() for v in str(form.get("facility_contains", "")).split(",") if v.strip()
    ]
    for name in ("product_ids", "business_unit_ids", "product_name_contains"):
        values[name] = [v.strip() for v in str(form.get(name, "")).split(",") if v.strip()]
    values["allow_waitlist"] = form.get("allow_waitlist") == "on"
    values["enabled"] = form.get("enabled") == "on"
    return RuleInput.model_validate(values)


@router.get("/rules")
def rules(request: Request, db: DB, user: CurrentUser):
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    rows = db.scalars(
        select(BookingRule).join(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    return render(request, "rules.html", user=user, rules=rows, accounts=accounts)


@router.get("/rules/new")
def new_rule(request: Request, db: DB, user: CurrentUser):
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    return render(request, "rule_form.html", user=user, accounts=accounts, rule=None)


@router.get("/rules/{rule_id}/edit")
def edit_rule(rule_id: int, request: Request, db: DB, user: CurrentUser):
    rule = owned_rule(db, user.id, rule_id)
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    return render(request, "rule_form.html", user=user, accounts=accounts, rule=rule)


@router.post("/rules")
def create_rule(request: Request, db: DB, user: CurrentUser, form: Form):
    return save_rule(None, request, db, user, form)


@router.post("/rules/{rule_id}")
def update_rule(rule_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    return save_rule(rule_id, request, db, user, form)


def save_rule(rule_id, request, db, user, form):
    rule = owned_rule(db, user.id, rule_id) if rule_id else None
    try:
        account_id = int(str(form.get("campushallen_account_id", "")))
    except ValueError:
        raise HTTPException(422, "Choose an account.") from None
    owned_account(db, user.id, account_id)
    try:
        data = parse_rule(form)
    except ValidationError as exc:
        accounts = db.scalars(
            select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
        ).all()
        # Retain submitted values, but never echo validation inputs containing secrets.
        from types import SimpleNamespace

        submitted = {key: value for key, value in form.items() if key != "csrf_token"}
        submitted["facility_contains"] = str(form.get("facility_contains", "")).split(",")
        for name in ("product_ids", "business_unit_ids", "product_name_contains"):
            submitted[name] = str(form.get(name, "")).split(",")
        submitted["allow_waitlist"] = form.get("allow_waitlist") == "on"
        submitted["enabled"] = form.get("enabled") == "on"
        for name in ("start_time", "preferred_start_time", "product_name"):
            submitted.setdefault(name, "")
        submitted["id"] = rule_id
        error = "; ".join(e["msg"] for e in exc.errors(include_input=False))
        return render(
            request,
            "rule_form.html",
            422,
            user=user,
            accounts=accounts,
            rule=SimpleNamespace(**submitted),
            error=error,
        )
    if rule is None:
        rule = BookingRule(campushallen_account_id=account_id, **data.model_dump())
        db.add(rule)
    else:
        rule.campushallen_account_id = account_id
        for name, value in data.model_dump().items():
            setattr(rule, name, value)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, "Rule names must be unique within an account.") from None
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    rule = owned_rule(db, user.id, rule_id)
    rule.enabled = not rule.enabled
    db.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    db.delete(owned_rule(db, user.id, rule_id))
    db.commit()
    return RedirectResponse("/rules", status_code=303)
