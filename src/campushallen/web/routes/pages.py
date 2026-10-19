from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from campushallen.database.models import (
    BookingAttempt,
    BookingRule,
    CampushallenAccount,
    CampushallenCredential,
    Outcome,
)
from campushallen.services.operations import account_cards, worker_health
from campushallen.web.dependencies import DB, CurrentUser, owned_account, render
from campushallen.web.i18n import gettext as _

router = APIRouter()


def recent_attempts(db, user_id, limit):
    return db.execute(
        select(BookingAttempt, CampushallenAccount.display_name)
        .join(CampushallenAccount)
        .where(CampushallenAccount.user_id == user_id)
        .order_by(BookingAttempt.created_at.desc(), BookingAttempt.id.desc())
        .limit(limit)
    ).all()


def rule_names(db, user_id):
    return dict(
        db.execute(
            select(BookingRule.id, BookingRule.name)
            .join(CampushallenAccount)
            .where(CampushallenAccount.user_id == user_id)
        ).all()
    )


@router.get("/")
def dashboard(request: Request, db: DB, user: CurrentUser):
    cards = account_cards(db, user.id)
    rules = db.scalars(
        select(BookingRule)
        .join(CampushallenAccount)
        .where(
            CampushallenAccount.user_id == user.id,
            CampushallenAccount.enabled.is_(True),
            BookingRule.enabled.is_(True),
        )
    ).all()
    remembered = set(
        db.scalars(
            select(CampushallenCredential.campushallen_account_id)
            .join(CampushallenAccount)
            .where(CampushallenAccount.user_id == user.id)
        )
    )
    recent_by_account = {}
    for account, slot in cards:
        recent_by_account[account.id] = db.scalar(
            select(BookingAttempt)
            .where(BookingAttempt.campushallen_account_id == account.id)
            .order_by(BookingAttempt.id.desc())
            .limit(1)
        )
    worker, state = worker_health(db, request.app.state.settings.worker_poll_seconds)
    return render(
        request,
        "dashboard.html",
        user=user,
        cards=cards,
        rules=rules,
        remembered=remembered,
        recent_by_account=recent_by_account,
        worker=worker,
        worker_state=state,
        recent=recent_attempts(db, user.id, 8),
        rule_names=rule_names(db, user.id),
    )


@router.get("/onboarding")
def onboarding(request: Request, db: DB, user: CurrentUser):
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    has_rule = (
        db.scalar(
            select(BookingRule.id)
            .join(CampushallenAccount)
            .where(CampushallenAccount.user_id == user.id)
            .limit(1)
        )
        is not None
    )
    has_preview = (
        db.scalar(
            select(BookingAttempt.id)
            .join(CampushallenAccount)
            .where(CampushallenAccount.user_id == user.id)
            .limit(1)
        )
        is not None
    )
    return render(
        request,
        "onboarding.html",
        user=user,
        accounts=accounts,
        has_rule=has_rule,
        has_preview=has_preview,
    )


@router.get("/history")
def history(
    request: Request,
    db: DB,
    user: CurrentUser,
    page: int = 1,
    account_id: str | None = None,
    result: str | None = None,
    activity_date: str | None = Query(None, alias="date"),
):
    page = max(1, min(page, 10000))
    query = (
        select(BookingAttempt, CampushallenAccount.display_name)
        .join(CampushallenAccount)
        .where(CampushallenAccount.user_id == user.id)
    )
    selected_account = None
    if account_id:
        try:
            selected_account = int(account_id)
        except ValueError:
            raise HTTPException(422, _("Invalid account filter.")) from None
        owned_account(db, user.id, selected_account)
        query = query.where(BookingAttempt.campushallen_account_id == selected_account)
    if result:
        if result not in {x.value for x in Outcome}:
            raise HTTPException(422, _("Unknown booking result."))
        query = query.where(BookingAttempt.result == result)
    if activity_date:
        try:
            start = datetime.combine(
                date.fromisoformat(activity_date),
                time.min,
                ZoneInfo(request.app.state.settings.campushallen_timezone),
            )
        except ValueError:
            raise HTTPException(422, _("Invalid activity date.")) from None
        query = query.where(
            BookingAttempt.activity_start >= start,
            BookingAttempt.activity_start < start + timedelta(days=1),
        )
    rows = db.execute(
        query.order_by(BookingAttempt.created_at.desc(), BookingAttempt.id.desc())
        .offset((page - 1) * 50)
        .limit(50)
    ).all()
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    filters = {
        k: v
        for k, v in {"account_id": account_id, "result": result, "date": activity_date}.items()
        if v is not None
    }
    return render(
        request,
        "history.html",
        user=user,
        recent=rows,
        page=page,
        accounts=accounts,
        account_id=selected_account,
        result=result,
        date_value=activity_date,
        outcomes=list(Outcome),
        rule_names=rule_names(db, user.id),
        previous_url="/history?" + urlencode({**filters, "page": page - 1}),
        next_url="/history?" + urlencode({**filters, "page": page + 1}),
    )
