from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import RedirectResponse

from campushallen.database.models import AuditEvent, CampushallenAccount, Invitation, utcnow
from campushallen.services.invitations import create_invite, revoke_invite
from campushallen.services.operations import worker_health
from campushallen.web.dependencies import DB, AdminUser, Form, render
from campushallen.web.i18n import gettext as _

router = APIRouter()


@router.get("/admin")
def admin_page(request: Request, db: DB, user: AdminUser):
    invitations = db.scalars(select(Invitation).order_by(Invitation.id.desc()).limit(200)).all()
    # Explicit safe columns; no cross-user account identifiers, tokens, or credentials.
    accounts = db.execute(
        select(
            CampushallenAccount.id,
            CampushallenAccount.auth_status,
            CampushallenAccount.enabled,
            CampushallenAccount.last_discovery_at,
        )
    ).all()
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(100)).all()
    return render(
        request,
        "admin.html",
        user=user,
        invitations=invitations,
        statuses=accounts,
        events=events,
        worker=worker_health(db, request.app.state.settings.worker_poll_seconds)[0],
    )


@router.post("/admin/invites")
def new_invites(request: Request, db: DB, user: AdminUser, form: Form):
    if not request.app.state.limiter.allow(f"invites-create:{user.id}"):
        raise HTTPException(429, _("Too many requests. Try again shortly."))
    try:
        count = int(str(form.get("count", "1")))
        days = int(str(form.get("expires_days", "7")))
        if not 1 <= count <= 100 or not 0 <= days <= 365:
            raise ValueError()
        links = []
        for index in range(count):
            row, token = create_invite(
                db,
                label=str(form.get("label", "")),
                created_by=user.id,
                expires_at=utcnow() + timedelta(days=days) if days else None,
            )
            links.append(f"{request.app.state.settings.public_base_url}/invite/{token}")
        db.commit()
    except ValueError:
        db.rollback()
        raise HTTPException(
            422,
            _(
                "Use 1–100 invites, a label up to 120 characters, and 0–365 expiry days (0 means no expiry).",
            ),
        ) from None
    # Plaintext links are rendered only in this response, never redirected through a URL or session.
    return render(request, "invite_created.html", user=user, links=links)


@router.post("/admin/invites/{invite_id}/revoke")
def revoke(invite_id: int, request: Request, db: DB, user: AdminUser, form: Form):
    revoke_invite(db, invite_id, actor_id=user.id)
    return RedirectResponse("/admin", status_code=303)
