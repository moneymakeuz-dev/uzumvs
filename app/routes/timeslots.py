from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.schemas import TimeslotWatchRequest
from app.services import uzum_accounts, uzum_timeslots
from app.web import current_user, verify_csrf

router = APIRouter(prefix="/api")
NO_STORE = {"Cache-Control": "no-store"}


@router.get("/timeslot-watches")
async def watches(request: Request):
    user = current_user(request, verified=True)
    async with request.app.state.db.transaction() as session:
        return JSONResponse({"watches": await uzum_timeslots.list_watches(session, user.id)}, headers=NO_STORE)


@router.post("/timeslot-watches", status_code=201)
async def create_watch(request: Request, payload: TimeslotWatchRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    await uzum_accounts.ensure_shop(request.app.state.db, request.app.state.settings, user.id, payload.shop_id)
    async with request.app.state.db.transaction() as session:
        watch = await uzum_timeslots.create_watch(session, user.id, payload)
    request.app.state.timeslot_wake.set()
    return JSONResponse(watch, status_code=201)


@router.post("/timeslot-watches/check", status_code=202)
async def check_now(request: Request):
    current_user(request, verified=True)
    verify_csrf(request)
    request.app.state.timeslot_wake.set()
    return JSONResponse({"status": "scheduled"}, status_code=202)


@router.post("/timeslot-watches/{watch_id}/cancel")
async def cancel_watch(request: Request, watch_id: UUID):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return await uzum_timeslots.cancel_watch(session, user.id, watch_id)
