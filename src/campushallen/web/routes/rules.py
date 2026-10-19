from datetime import time
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import FormData
from starlette.responses import RedirectResponse

from campushallen.database.models import BookingOpportunity, BookingRule, CampushallenAccount
from campushallen.services.audit import Event, audit
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
    if not fields["earliest_allowed_time"] or not fields["latest_allowed_time"]:
        value = fields.get("preferred_start_time") or fields.get("start_time")
        preferred = TypeAdapter(time).validate_python(value) if value else None
        minutes = preferred.hour * 60 + preferred.minute if preferred else None
        lower = max(0, minutes - 60) if minutes is not None else 0
        upper = min(1439, minutes + 60) if minutes is not None else 1439
        values["earliest_allowed_time"] = fields["earliest_allowed_time"] or time(
            lower // 60, lower % 60
        )
        values["latest_allowed_time"] = fields["latest_allowed_time"] or time(
            upper // 60, upper % 60
        )
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


@router.get("/rules/from-activity/{opportunity_id}")
def from_activity(opportunity_id: int, request: Request, db: DB, user: CurrentUser):
    slot = db.scalar(
        select(BookingOpportunity)
        .join(CampushallenAccount)
        .where(BookingOpportunity.id == opportunity_id, CampushallenAccount.user_id == user.id)
    )
    if not slot:
        raise HTTPException(404, "Activity not found")
    local = slot.activity_start.astimezone(
        ZoneInfo(request.app.state.settings.campushallen_timezone)
    )
    source_rule = db.get(BookingRule, slot.booking_rule_id)
    if source_rule is None:
        raise HTTPException(404, "Source rule no longer exists")
    values = {
        "id": None,
        "campushallen_account_id": slot.campushallen_account_id,
        "name": f"{slot.product_name} {local:%A}"[:120],
        "activity": slot.product_name,
        "product_name": slot.product_name,
        "weekday": local.weekday(),
        "booking_type": "group_activity",
        "web_category_id": source_rule.web_category_id,
        "facility_contains": [x.strip() for x in slot.facility.split(",") if x.strip()],
        "preferred_start_time": local.time(),
        "start_time": None,
        "earliest_allowed_time": "",
        "latest_allowed_time": "",
        "product_ids": [],
        "business_unit_ids": [],
        "product_name_contains": [],
        "enabled": True,
        "allow_waitlist": False,
    }
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    return render(
        request, "rule_form.html", user=user, accounts=accounts, rule=SimpleNamespace(**values)
    )


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
        submitted = {key: value for key, value in form.items() if key != "csrf_token"}
        submitted["facility_contains"] = str(form.get("facility_contains", "")).split(",")
        for name in ("product_ids", "business_unit_ids", "product_name_contains"):
            submitted[name] = str(form.get(name, "")).split(",")
        submitted["allow_waitlist"] = form.get("allow_waitlist") == "on"
        submitted["enabled"] = form.get("enabled") == "on"
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
        ):
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
        db.execute(delete(BookingOpportunity).where(BookingOpportunity.booking_rule_id == rule.id))
        rule.campushallen_account_id = account_id
        for name, value in data.model_dump().items():
            setattr(rule, name, value)
    audit(
        db,
        Event.BOOKING_RULE_UPDATED if rule_id else Event.BOOKING_RULE_CREATED,
        user_id=user.id,
        actor_id=user.id,
        account_id=account_id,
    )
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(409, "Rule names must be unique within an account.") from None
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    rule = owned_rule(db, user.id, rule_id)
    rule.enabled = not rule.enabled
    audit(
        db,
        Event.BOOKING_RULE_UPDATED,
        user_id=user.id,
        actor_id=user.id,
        account_id=rule.campushallen_account_id,
    )
    db.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int, request: Request, db: DB, user: CurrentUser, form: Form):
    rule = owned_rule(db, user.id, rule_id)
    audit(
        db,
        Event.BOOKING_RULE_DELETED,
        user_id=user.id,
        actor_id=user.id,
        account_id=rule.campushallen_account_id,
    )
    db.delete(rule)
    db.commit()
    return RedirectResponse("/rules", status_code=303)
