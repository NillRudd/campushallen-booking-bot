"""Owned, read-only browsing and explicit confirmation of recurring rules."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from campushallen.database.models import BookingRule, CampushallenAccount
from campushallen.services.audit import Event, audit
from campushallen.services.discovery import (
    ActivityOccurrence,
    DiscoveryUnavailable,
    same_occurrence_rule,
    search_occurrences,
    selection_key,
)
from campushallen.services.secrets import ReconnectRequired
from campushallen.web.dependencies import DB, CurrentUser, Form, render
from campushallen.web.i18n import gettext as _
from campushallen.web.i18n import weekdays

router = APIRouter()


def search_date(request, value):
    today = datetime.now(ZoneInfo(request.app.state.settings.campushallen_timezone)).date()
    try:
        return date.fromisoformat(value) if value else today
    except ValueError:
        raise HTTPException(422, _("Choose a valid date.")) from None


def sole_account(db, user):
    return db.scalar(select(CampushallenAccount).where(CampushallenAccount.user_id == user.id))


@router.get("/activities")
def browse(
    request: Request,
    db: DB,
    user: CurrentUser,
    q: str = Query("", max_length=150),
    facility: str = Query("", max_length=150),
    start: str = "",
    days: int = Query(7, ge=1, le=21),
):
    account = sole_account(db, user)
    first = search_date(request, start)
    occurrences: tuple[ActivityOccurrence, ...] = ()
    error = None
    if account:
        try:
            occurrences = request.app.state.discovery.upcoming(account, first, days)
        except ReconnectRequired:
            error = _("Connect or enable your Campushallen account before browsing sessions.")
        except (httpx.HTTPError, DiscoveryUnavailable):
            error = _("The schedule could not be loaded. Try again or choose a shorter date range.")
        except ValueError:
            raise HTTPException(
                422, _("Choose a date and range within the next 21 days.")
            ) from None
    else:
        error = _("Connect your Campushallen account to find sessions. No rule is needed.")
    groups: dict[tuple, list] = {}
    for occurrence in search_occurrences(occurrences, q, facility):
        groups.setdefault((occurrence.product_name, occurrence.location), []).append(occurrence)
    return render(
        request,
        "activities.html",
        user=user,
        account=account,
        groups=groups,
        facilities=sorted({item.location for item in occurrences}),
        q=q,
        facility=facility,
        start=first.isoformat(),
        days=days,
        error=error,
    )


@router.post("/activities/select")
def select_activities(request: Request, db: DB, user: CurrentUser, form: Form):
    account = sole_account(db, user)
    if account is None:
        raise HTTPException(409, _("Connect your Campushallen account first."))
    try:
        selected = {int(str(value)) for value in form.getlist("activity_ids")}
        days = int(str(form.get("days", "7")))
    except ValueError:
        raise HTTPException(422, _("Choose at least one session.")) from None
    if not selected or len(selected) > 21:
        raise HTTPException(422, _("Choose between one and 21 sessions."))
    start = search_date(request, str(form.get("start", "")))
    try:
        occurrences = request.app.state.discovery.upcoming(account, start, days)
    except ReconnectRequired:
        raise HTTPException(409, _("Reconnect your Campushallen account and try again.")) from None
    except (httpx.HTTPError, DiscoveryUnavailable):
        raise HTTPException(503, _("The schedule could not be loaded. Try again later.")) from None
    except ValueError:
        raise HTTPException(422, _("Choose a date and range within the next 21 days.")) from None
    choices = [item for item in occurrences if item.activity_id in selected]
    if {item.activity_id for item in choices} != selected:
        raise HTTPException(409, _("The selected session is no longer available. Search again."))
    existing = list(
        db.scalars(select(BookingRule).where(BookingRule.campushallen_account_id == account.id))
    )
    created, duplicates = [], []
    for occurrence in choices:
        duplicate = next(
            (rule for rule in existing if same_occurrence_rule(rule, occurrence)), None
        )
        if duplicate:
            if duplicate not in duplicates and duplicate not in created:
                duplicates.append(duplicate)
            continue
        data = occurrence.rule_input()
        day = weekdays(request.app.state.settings.default_locale)[data.weekday]
        suffix = f" · {day} {occurrence.start:%H:%M}"
        data.name = f"{data.product_name[: 120 - len(suffix)]}{suffix}"
        data.allow_waitlist = form.get("allow_waitlist") == "on"
        rule = BookingRule(
            campushallen_account_id=account.id,
            selection_key=selection_key(data),
            **data.model_dump(),
        )
        existing.append(rule)
        created.append(rule)
        db.add(rule)
        audit(
            db, Event.BOOKING_RULE_CREATED, user_id=user.id, actor_id=user.id, account_id=account.id
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, _("You already have a booking rule for this session.")) from None
    return render(
        request, "activity_rules_created.html", user=user, created=created, duplicates=duplicates
    )
