from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from campushallen.database.models import WebSession
from campushallen.services.authentication import authenticate, new_session, token_hash
from campushallen.web.dependencies import DB, Form, render

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, db: DB, form: Form):
    key = request.client.host if request.client else "local"
    if not request.app.state.limiter.allow(key):
        raise HTTPException(429, "Too many login attempts. Try again in five minutes.")
    password = str(form.get("password", ""))
    if len(password) > 1024:
        return render(request, "login.html", 400, error="Invalid username or password.")
    user = authenticate(db, str(form.get("username", "")), password)
    if not user:
        return render(request, "login.html", 400, error="Invalid username or password.")
    old = db.get(
        WebSession, token_hash(request.app.state.secret, request.cookies.get("session", ""))
    )
    if old:
        db.delete(old)
    settings = request.app.state.settings
    token = new_session(db, user, request.app.state.secret, settings.session_hours)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_hours * 3600,
    )
    response.delete_cookie("csrf")
    return response


@router.post("/logout")
def logout(request: Request, db: DB, form: Form):
    session = db.get(
        WebSession, token_hash(request.app.state.secret, request.cookies.get("session", ""))
    )
    if session:
        db.delete(session)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    response.delete_cookie("csrf")
    return response
