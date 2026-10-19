from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from campushallen.services.invitations import InvalidInvitation, consume_invite, validate_invite
from campushallen.web.dependencies import DB, Form, render
from campushallen.web.i18n import gettext as _
from campushallen.web.presentation import safe_error

router = APIRouter()


def limited(request: Request, action: str) -> None:
    host = request.client.host if request.client else "local"
    if not request.app.state.limiter.allow(f"{action}:{host}"):
        raise HTTPException(429, _("Too many attempts. Try again in five minutes."))


@router.get("/invite/{token}")
def invite_page(token: str, request: Request, db: DB):
    limited(request, "invite")
    try:
        validate_invite(db, token)
    except InvalidInvitation as exc:
        return render(request, "invite.html", 400, error=safe_error(str(exc)), available=False)
    return render(request, "invite.html", available=True)


@router.post("/invite/{token}")
def register(token: str, request: Request, db: DB, form: Form):
    limited(request, "register")
    try:
        # Validate the token before giving registration-specific feedback.
        validate_invite(db, token)
        password = str(form.get("password", ""))
        if password != str(form.get("confirmation", "")):
            raise ValueError("Website passwords do not match.")
        consume_invite(db, token, str(form.get("username", "")), password)
    except InvalidInvitation as exc:
        return render(request, "invite.html", 400, error=safe_error(str(exc)), available=False)
    except ValueError as exc:
        return render(request, "invite.html", 422, error=safe_error(str(exc)), available=True)
    # Registration does not inherit an existing browser login. Explicit login creates a fresh session.
    return RedirectResponse("/login?registered=1", status_code=303)
