from uuid import UUID

from fastapi import APIRouter, Query, Request
from starlette.responses import JSONResponse

from app.schemas import TimeslotWatchRequest
from app.services import uzum_seller, uzum_timeslots
from app.web import uzum_user, verify_csrf

router = APIRouter(prefix="/api")
NO_STORE = {"Cache-Control": "no-store"}


@router.get("/uzum/shops")
async def shops(request: Request):
    uzum_user(request)
    return JSONResponse({"shops": await uzum_seller.list_shops(request.app.state.settings)}, headers=NO_STORE)


@router.get("/uzum/invoices")
async def invoices(request: Request, shop_id: str = Query(..., pattern=r"^\d{1,32}$")):
    uzum_user(request)
    items = await uzum_timeslots.list_invoices(request.app.state.settings, shop_id)
    return JSONResponse({"invoices": items}, headers=NO_STORE)


@router.get("/timeslot-watches")
async def watches(request: Request):
    user = uzum_user(request)
    async with request.app.state.db.transaction() as session:
        return JSONResponse({"watches": await uzum_timeslots.list_watches(session, user.id)}, headers=NO_STORE)


@router.post("/timeslot-watches", status_code=201)
async def create_watch(request: Request, payload: TimeslotWatchRequest):
    user = uzum_user(request)
    verify_csrf(request)
    await uzum_timeslots.ensure_shop(request.app.state.settings, payload.shop_id)
    async with request.app.state.db.transaction() as session:
        watch = await uzum_timeslots.create_watch(session, user.id, payload)
    request.app.state.timeslot_wake.set()
    return JSONResponse(watch, status_code=201)


@router.post("/timeslot-watches/check", status_code=202)
async def check_now(request: Request):
    uzum_user(request)
    verify_csrf(request)
    request.app.state.timeslot_wake.set()
    return JSONResponse({"status": "scheduled"}, status_code=202)


@router.post("/timeslot-watches/{watch_id}/cancel")
async def cancel_watch(request: Request, watch_id: UUID):
    user = uzum_user(request)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return await uzum_timeslots.cancel_watch(session, user.id, watch_id)
