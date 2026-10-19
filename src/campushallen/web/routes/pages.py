from fastapi import APIRouter, Request
from sqlalchemy import select

from campushallen.database.models import BookingAttempt, BookingRule, CampushallenAccount, utcnow
from campushallen.web.dependencies import DB, CurrentUser, render

router = APIRouter()


def recent_attempts(db, user_id, limit):
    return db.execute(
        select(BookingAttempt, CampushallenAccount.display_name)
        .join(CampushallenAccount)
        .where(CampushallenAccount.user_id == user_id)
        .order_by(BookingAttempt.created_at.desc(), BookingAttempt.id.desc())
        .limit(limit)
    ).all()


@router.get("/")
def dashboard(request: Request, db: DB, user: CurrentUser):
    accounts = db.scalars(
        select(CampushallenAccount).where(CampushallenAccount.user_id == user.id)
    ).all()
    rules = db.scalars(
        select(BookingRule)
        .join(CampushallenAccount)
        .where(
            CampushallenAccount.user_id == user.id,
            CampushallenAccount.enabled.is_(True),
            BookingRule.enabled.is_(True),
        )
    ).all()
    upcoming = db.scalar(
        select(BookingAttempt)
        .join(CampushallenAccount)
        .join(BookingRule, BookingAttempt.booking_rule_id == BookingRule.id)
        .where(
            CampushallenAccount.user_id == user.id,
            CampushallenAccount.enabled.is_(True),
            BookingRule.enabled.is_(True),
            BookingAttempt.activity_start > utcnow(),
            BookingAttempt.result == "DRY_RUN",
        )
        .order_by(BookingAttempt.activity_start, BookingAttempt.created_at.desc())
        .limit(1)
    )
    return render(
        request,
        "dashboard.html",
        user=user,
        accounts=accounts,
        rules=rules,
        recent=recent_attempts(db, user.id, 8),
        upcoming=upcoming,
    )


@router.get("/history")
def history(request: Request, db: DB, user: CurrentUser, page: int = 1):
    page = max(1, min(page, 10000))
    rows = db.execute(
        select(BookingAttempt, CampushallenAccount.display_name)
        .join(CampushallenAccount)
        .where(CampushallenAccount.user_id == user.id)
        .order_by(BookingAttempt.created_at.desc(), BookingAttempt.id.desc())
        .offset((page - 1) * 50)
        .limit(50)
    ).all()
    return render(request, "history.html", user=user, recent=rows, page=page)
