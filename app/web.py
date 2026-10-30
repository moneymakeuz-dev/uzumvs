from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.config import ROOT, Settings
from app.errors import AppError
from app.models import User
from app.security import csrf_matches
from app.services.auth import Identity

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def context(request: Request, **values: Any) -> dict[str, Any]:
    identity: Identity | None = getattr(request.state, "identity", None)
    user = identity.user if identity else None
    return {"request": request, "user": user,
            "csrf": identity.csrf if identity else "", "settings": request.app.state.settings,
            "uzum_access": bool(user and uzum_allowed(request.app.state.settings, user)),
            "active_page": "", **values}


def uzum_allowed(settings: Settings, user: User) -> bool:
    allowed = {item.strip().casefold() for item in settings.uzum_allowed_emails.split(",") if item.strip()}
    return bool(user.email_verified_at) and (not allowed or user.email_normalized.casefold() in allowed)


def uzum_user(request: Request) -> User:
    user = current_user(request, verified=True)
    if not uzum_allowed(request.app.state.settings, user):
        raise AppError("uzum_forbidden", "Uzum do'koniga yuborish faqat do'kon egasiga ruxsat etilgan.", 403)
    return user


def current_user(request: Request, verified: bool = False) -> User:
    identity: Identity = request.state.identity
    if not identity.user:
        raise AppError("unauthorized", "Hisobingizga kiring.", 401)
    if verified and not identity.user.email_verified_at:
        raise AppError("email_unverified", "Avval email manzilingizni tasdiqlang.", 403)
    return identity.user


def verify_csrf(request: Request, form_token: str | None = None) -> None:
    origin = request.headers.get("origin")
    expected_origin = request.app.state.settings.app_base_url.rstrip("/")
    cross_site = request.headers.get("sec-fetch-site") == "cross-site"
    if cross_site or (origin and origin != "null" and origin != expected_origin):
        raise AppError("csrf_failed", "Sahifani yangilab, qayta urinib ko'ring.", 403)
    token = request.headers.get("x-csrf-token") or form_token
    if not csrf_matches(request.state.identity.csrf, token):
        raise AppError("csrf_failed", "Sahifani yangilab, qayta urinib ko'ring.", 403)


def cookie_name(settings: Settings) -> str:
    return "__Host-karto_session" if settings.secure_cookies else "karto_session"


def set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(cookie_name(settings), token, max_age=7 * 24 * 3600,
                        httponly=True, secure=settings.secure_cookies, samesite="lax", path="/")


def clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(cookie_name(settings), path="/", httponly=True,
                           secure=settings.secure_cookies, samesite="lax")
