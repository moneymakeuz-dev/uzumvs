from fastapi import APIRouter, Request
from sqlalchemy import select, update
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse

from app.clock import utcnow
from app.config import Settings
from app.errors import AppError, not_found
from app.models import LoginSession, User
from app.security import normalize_email
from app.services import auth, mail
from app.web import clear_cookie, context, current_user, set_cookie, templates, verify_csrf

router = APIRouter(prefix="/auth")
ACTIONS = {"login", "register", "verify-email", "forgot-password", "reset-password"}


def render_form(request: Request, action: str, error: str | None = None, status: int = 200,
                notice: str | None = None, token: str = ""):
    return templates.TemplateResponse(request, "auth.html", context(
        request, action=action, token=token[:128], notice=notice, error=error, auth_page=True,
        title="Kirish" if action == "login" else "Hisob"), status_code=status)


@router.get("/{action}")
async def auth_page(request: Request, action: str):
    if action not in ACTIONS:
        raise not_found()
    if request.state.identity.user and action in {"login", "register"}:
        return RedirectResponse("/cards/new", status_code=303)
    notices = {"sent": "Manzil mos bo'lsa, tasdiqlash havolasi yuboriladi.",
               "verified": "Email tasdiqlandi. Endi hisobingizga kiring.",
               "reset": "Parol yangilandi. Yangi parol bilan kiring."}
    return render_form(request, action, notice=notices.get(request.query_params.get("notice", "")),
                       token=request.query_params.get("token", ""))


async def form_data(request: Request) -> dict[str, str]:
    form = await request.form(max_files=0, max_fields=8)
    values = {key: str(value) for key, value in form.items()}
    verify_csrf(request, values.get("csrf_token"))
    return values


def limit(request: Request, action: str, email: str = "") -> None:
    host = request.client.host if request.client else "unknown"
    rate = request.app.state.rates
    rate.hit("20 per 15 minutes" if action == "login" else "10 per hour", action, host)
    if action != "login" and email:
        rate.hit("3 per hour", f"email:{action}", email.casefold())


@router.post("/login")
async def login(request: Request):
    values = await form_data(request)
    email = values.get("email", "")[:254]
    try:
        limit(request, "login")
        request.app.state.rates.test("5 per 15 minutes", "login-fail", email.casefold())
        async with request.app.state.db.transaction() as session:
            user = await auth.authenticate(session, email, values.get("password", ""))
            token = await auth.rotate_session(session, request.state.identity, user)
    except AppError as error:
        if error.code == "invalid_login":
            request.app.state.rates.hit("5 per 15 minutes", "login-fail", email.casefold())
        return render_form(request, "login", error.message, error.status)
    response = RedirectResponse("/cards/new" if user.email_verified_at else "/profile", status_code=303)
    set_cookie(response, request.app.state.settings, token)
    return response


@router.post("/register")
async def register(request: Request):
    values = await form_data(request)
    try:
        email = normalize_email(values.get("email", "")[:254])
        limit(request, "register", email)
        async with request.app.state.db.transaction() as session:
            user = await auth.register(session, email, values.get("password", ""))
            token = await auth.issue_auth_token(session, user, "verify_email") if user else None
    except AppError as error:
        return render_form(request, "register", error.message, error.status)
    return delivery_redirect("/auth/login?notice=sent", request.app.state.settings, email, token, "verify_email")


def delivery_redirect(location: str, settings: Settings, email: str, token: str | None, purpose: str):
    background = BackgroundTask(deliver, settings, email, token, purpose) if token else None
    return RedirectResponse(location, status_code=303, background=background)


async def deliver(settings: Settings, email: str, token: str, purpose: str) -> None:
    try:
        await mail.send_auth_link(settings, email, token, purpose)
    except AppError:
        return


@router.post("/logout")
async def logout(request: Request):
    await form_data(request)
    async with request.app.state.db.transaction() as session:
        await session.execute(update(LoginSession).where(LoginSession.id == request.state.identity.session_id)
                              .values(revoked_at=utcnow()))
    response = RedirectResponse("/auth/login", status_code=303)
    clear_cookie(response, request.app.state.settings)
    return response


@router.post("/verify-email")
async def verify_email(request: Request):
    values = await form_data(request)
    token = values.get("token", "")[:128]
    try:
        limit(request, "verify")
        async with request.app.state.db.transaction() as session:
            await auth.verify_email(session, token)
    except AppError as error:
        return render_form(request, "verify-email", error.message, error.status, token=token)
    return RedirectResponse("/auth/login?notice=verified", status_code=303)


@router.post("/verification-email")
async def resend(request: Request):
    await form_data(request)
    user = current_user(request)
    limit(request, "resend", user.email_normalized)
    token = None
    if not user.email_verified_at:
        async with request.app.state.db.transaction() as session:
            token = await auth.issue_auth_token(session, user, "verify_email")
    return delivery_redirect("/profile?notice=sent", request.app.state.settings, user.email_normalized, token, "verify_email")


@router.post("/forgot-password")
async def forgot(request: Request):
    values = await form_data(request)
    try:
        email = normalize_email(values.get("email", "")[:254])
        limit(request, "forgot", email)
        async with request.app.state.db.transaction() as session:
            user = await session.scalar(select(User).where(User.email_normalized == email, User.deletion_requested_at.is_(None)))
            token = await auth.issue_auth_token(session, user, "reset_password") if user else None
    except AppError as error:
        return render_form(request, "forgot-password", error.message, error.status)
    return delivery_redirect("/auth/forgot-password?notice=sent", request.app.state.settings, email, token, "reset_password")


@router.post("/reset-password")
async def reset(request: Request):
    values = await form_data(request)
    token = values.get("token", "")[:128]
    try:
        limit(request, "reset")
        async with request.app.state.db.transaction() as session:
            await auth.reset_password(session, token, values.get("password", ""))
    except AppError as error:
        return render_form(request, "reset-password", error.message, error.status, token=token)
    response = RedirectResponse("/auth/login?notice=reset", status_code=303)
    clear_cookie(response, request.app.state.settings)
    return response
