import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from app.config import ROOT, Settings, get_settings
from app.db import Database
from app.errors import AppError
from app.middleware import BodyLimitMiddleware, RateLimits, SecurityMiddleware
from app.routes import auth, cards, pages, profile
from app.web import context, templates

logger = logging.getLogger(__name__)


async def app_error(request: Request, error: AppError):
    request_id = getattr(request.state, "request_id", "")
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else {}
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": {"code": error.code, "message": error.message,
                                       "fields": error.fields, "request_id": request_id}},
                            status_code=error.status, headers=headers)
    if error.status == 401:
        if request.headers.get("hx-request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/auth/login"})
        return RedirectResponse("/auth/login", status_code=303)
    return templates.TemplateResponse(request, "error.html", context(
        request, error=error, title="Xatolik"), status_code=error.status, headers=headers)


async def validation_error(request: Request, error: RequestValidationError):
    fields = {".".join(str(part) for part in item["loc"]): "Maydon qiymatini tekshiring."
              for item in error.errors()}
    return await app_error(request, AppError("validation_error", "Kiritilgan ma'lumotlarni tekshiring.", fields=fields))


async def http_error(request: Request, error: HTTPException):
    return await app_error(request, AppError("invalid_request", "So'rov qabul qilinmadi.", error.status_code))


async def unexpected_error(request: Request, error: Exception):
    logger.error("request_failed id=%s type=%s", getattr(request.state, "request_id", ""), type(error).__name__)
    return await app_error(request, AppError("service_unavailable", "Xizmat vaqtincha ishlamayapti. Qayta urinib ko'ring.", 503))


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = database or Database(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        if database is None:
            await db.close()

    app = FastAPI(title="Karto Studio", lifespan=lifespan,
                  docs_url=None if settings.app_env == "production" else "/api/docs", redoc_url=None)
    app.state.settings, app.state.db, app.state.rates = settings, db, RateLimits()
    app.add_exception_handler(AppError, app_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected_error)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*settings.allowed_hosts.split(","), "127.0.0.1"])
    app.include_router(auth.router)
    app.include_router(cards.router)
    app.include_router(profile.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static"), check_dir=False), name="static")
    add_health_routes(app)
    return app


def add_health_routes(app: FastAPI) -> None:
    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request):
        try:
            healthy = await request.app.state.db.ready()
        except SQLAlchemyError:
            healthy = False
        return JSONResponse({"status": "ready" if healthy else "unavailable"}, status_code=200 if healthy else 503)


app = create_app()
