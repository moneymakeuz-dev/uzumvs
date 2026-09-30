import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.errors import AppError
from app.security import token_hash
from app.services.auth import ANONYMOUS, anonymous_identity, find_identity
from app.web import cookie_name, set_cookie

logger = logging.getLogger(__name__)
TOKEN_PAGES = {"/auth/verify-email", "/auth/reset-password"}


class RateLimits:
    def __init__(self) -> None:
        self.storage = MemoryStorage()
        self.limiter = FixedWindowRateLimiter(self.storage)

    def hit(self, rule: str, scope: str, identity: str) -> None:
        if not self.limiter.hit(parse(rule), scope, token_hash(identity)):
            raise AppError("rate_limited", "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.", 429, retry_after=900)

    def test(self, rule: str, scope: str, identity: str) -> None:
        if not self.limiter.test(parse(rule), scope, token_hash(identity)):
            raise AppError("rate_limited", "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.", 429, retry_after=900)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = perf_counter()
        new_cookie = None
        settings = request.app.state.settings
        request.state.identity = ANONYMOUS
        if not request.url.path.startswith(("/static/", "/health/")):
            try:
                async with request.app.state.db.transaction() as session:
                    identity = await find_identity(session, request.cookies.get(cookie_name(settings)))
                    if identity is None and request.url.path.startswith("/auth/"):
                        host = request.client.host if request.client else "unknown"
                        request.app.state.rates.hit("30 per minute", "anonymous-session", host)
                        identity, new_cookie = await anonymous_identity(session)
                request.state.identity = identity or ANONYMOUS
            except AppError as error:
                return JSONResponse({"error": {"code": error.code, "message": error.message}},
                                    status_code=error.status, headers={"Retry-After": str(error.retry_after or 60)})
            except SQLAlchemyError:
                logger.error("database_unavailable request_id=%s", request.state.request_id)
                return JSONResponse({"error": {"code": "database_unavailable", "message": "Xizmat vaqtincha ishlamayapti.",
                                               "request_id": request.state.request_id}}, status_code=503)
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            logger.info("request id=%s method=%s path=%s status=%s ms=%d", request.state.request_id, request.method,
                        request.url.path, response.status_code, (perf_counter() - started) * 1000)
        existing_cookie = any(value.startswith(cookie_name(settings) + "=") for value in response.headers.getlist("set-cookie"))
        if new_cookie and not existing_cookie:
            set_cookie(response, settings, new_cookie)
        response.headers.update({
            "X-Request-ID": request.state.request_id, "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer" if request.url.path in TOKEN_PAGES else "same-origin", "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        })
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


class BodyLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = 52 * 1024 * 1024 if scope["path"] == "/api/cards" else 2 * 1024 * 1024
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise AppError("request_size", "So'rov hajmi juda katta.", 413)
            return message

        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > limit:
                response = JSONResponse({"error": {"code": "request_size", "message": "So'rov hajmi juda katta."}}, status_code=413)
                return await response(scope, receive, send)
        except ValueError:
            return await JSONResponse({"error": {"code": "invalid_request"}}, status_code=400)(scope, receive, send)
        await self.app(scope, limited_receive, send)
