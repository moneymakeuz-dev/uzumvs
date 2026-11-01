from fastapi import APIRouter, Query, Request
from starlette.responses import JSONResponse, Response

from app.routes.pages import page_context
from app.schemas import UzumConnectRequest
from app.services import uzum_accounts, uzum_timeslots
from app.services.maintenance import audit
from app.web import current_user, templates, verify_csrf

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}


def safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith(("//", "/\\")) else "/cards/new"


@router.get("/uzum/connect")
async def connect_page(request: Request, next: str = Query("/cards/new", max_length=200)):
    user = current_user(request, verified=True)
    async with request.app.state.db.transaction() as session:
        account = uzum_accounts.account_view(await uzum_accounts.find(session, user.id))
    return templates.TemplateResponse(request, "uzum_connect.html", await page_context(
        request, title="Uzum ulanishi", page_name="Uzum ulanishi", active_page="uzum", account=account,
        next_url=safe_next(next)), headers=NO_STORE)


@router.post("/api/uzum/account")
async def connect(request: Request, payload: UzumConnectRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    # Uzum parollarini tanlash uchun xizmatdan foydalanishning oldini oladi (Uzum ham ko'p urinishni bloklaydi).
    request.app.state.rates.hit("5 per 15 minutes", "uzum-connect", str(user.id))
    account = await uzum_accounts.connect(request.app.state.db, request.app.state.settings, user.id,
                                          payload.api_token, payload.login, payload.password)
    audit(request.app.state.settings, "uzum_connected", {"user": str(user.id), "shops": len(account["shops"])})
    return JSONResponse(account, headers=NO_STORE)


@router.delete("/api/uzum/account", status_code=204)
async def disconnect(request: Request):
    user = current_user(request, verified=True)
    verify_csrf(request)
    await uzum_accounts.disconnect(request.app.state.db, user.id)
    audit(request.app.state.settings, "uzum_disconnected", {"user": str(user.id)})
    return Response(status_code=204)


@router.get("/api/uzum/shops")
async def shops(request: Request):
    user = current_user(request, verified=True)
    items = await uzum_accounts.shops(request.app.state.db, request.app.state.settings, user.id)
    return JSONResponse({"shops": items}, headers=NO_STORE)


@router.get("/api/uzum/invoices")
async def invoices(request: Request, shop_id: str = Query(..., pattern=r"^\d{1,32}$")):
    user = current_user(request, verified=True)
    items = await uzum_timeslots.list_invoices(request.app.state.db, request.app.state.settings, user.id, shop_id)
    return JSONResponse({"invoices": items}, headers=NO_STORE)
