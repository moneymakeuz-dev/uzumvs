from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Query, Request
from starlette.responses import RedirectResponse

from app.errors import AppError
from app.services import cards, quotas
from app.web import context, current_user, templates

router = APIRouter()
JOB_LABELS = {"queued": "Navbatda", "running": "Yaratilmoqda", "succeeded": "Tayyor",
              "failed": "Xato", "cancelled": "Bekor qilindi"}
JOB_ERRORS = {
    "provider_timeout": "AI xizmati vaqtida javob bermadi. Qayta urinib ko'ring.",
    "provider_unavailable": "AI xizmati vaqtincha band. Birozdan keyin qayta urinib ko'ring.",
    "invalid_ai_response": "AI javobi talablarga mos kelmadi. Izohga aniq faktlar qo'shib qayta urinib ko'ring.",
    "budget_exhausted": "AI xizmatining bugungi budjeti tugagan. Keyinroq qayta urinib ko'ring.",
    "worker_interrupted": "Vazifa uzilib qoldi. Limit qaytarildi, qayta urinib ko'ring.",
    "queue_expired": "Navbatni kutish vaqti tugadi. Limit qaytarildi.",
    "ai_not_configured": "AI xizmati ulanmagan. Administrator bilan bog'laning.",
    "configuration_changed": "AI sozlamalari yangilandi. Qayta urinib ko'ring.",
    "image_missing": "Rasm fayli topilmadi. Yangi kartochka yarating.",
    "input_too_large": "Ma'lumot hajmi katta. Izohni qisqartirib qayta urinib ko'ring.",
    "version_conflict": "Kartochka vazifa davomida o'zgargan, natija qo'llanmadi.",
    "generation_failed": "Kartochkani yaratib bo'lmadi. Qayta urinib ko'ring.",
}


def job_view(card: dict[str, Any]) -> dict[str, Any]:
    job = card["latest_job"]
    status = job["status"] if job else None
    message = ""
    if status == "failed":
        message = JOB_ERRORS.get(job["error_code"], JOB_ERRORS["generation_failed"])
        if card["status"] == "ready":
            message += " Oldingi matn o'zgarishsiz qoldi."
    return {"job": job, "status": status, "label": JOB_LABELS.get(status, ""), "message": message,
            "active": status in {"queued", "running"},
            "can_generate": card["status"] == "draft" and status not in {"queued", "running"}}


@router.get("/")
async def home(request: Request):
    return RedirectResponse("/cards/new" if request.state.identity.user else "/auth/login", status_code=303)


async def page_context(request: Request, **values):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        usage = await quotas.usage(session, user.id, request.app.state.settings)
    return context(request, usage=usage, **values)


@router.get("/cards/new")
async def new_card(request: Request):
    return templates.TemplateResponse(request, "new.html", await page_context(
        request, title="Yangi kartochka", page_name="Yangi kartochka", active_page="new"))


@router.get("/cards")
async def history(request: Request, q: str = Query("", max_length=200),
                  status: str = Query("", pattern="^(|draft|ready|queued|running|failed)$"), page: int = Query(1, ge=1)):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        result = await cards.list_cards(session, user.id, q, status, page)
    pages = max(1, (result["total"] + 19) // 20)
    if result["total"] and page > pages:
        return RedirectResponse("/cards?" + urlencode({"q": q, "status": status, "page": pages}), status_code=303)
    previous = "/cards?" + urlencode({"q": q, "status": status, "page": max(1, page - 1)})
    following = "/cards?" + urlencode({"q": q, "status": status, "page": page + 1})
    return templates.TemplateResponse(request, "history.html", await page_context(
        request, title="Kartochkalar", page_name="Kartochkalar", active_page="history", result=result,
        q=q, status=status, pages=pages, previous=previous, following=following))


@router.get("/cards/{card_id}")
async def card_page(request: Request, card_id: UUID):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        card = await cards.card_payload(session, await cards.owned_card(session, user.id, card_id))
    title = card["content"]["title"]["uz"] if card["content"] else "Yangi draft"
    return templates.TemplateResponse(request, "card.html", await page_context(
        request, title=title, page_name="Kartochka", active_page="history", card=card, view=job_view(card)))


@router.get("/cards/{card_id}/status")
async def card_status(request: Request, card_id: UUID):
    user = current_user(request)
    try:
        async with request.app.state.db.transaction() as session:
            card = await cards.card_payload(session, await cards.owned_card(session, user.id, card_id))
    except AppError as error:
        if error.status != 404:
            raise
        return templates.TemplateResponse(request, "partials/job_missing.html", context(request))
    return templates.TemplateResponse(request, "partials/job_status.html", context(
        request, card=card, view=job_view(card)))


@router.get("/profile")
async def profile(request: Request):
    return templates.TemplateResponse(request, "profile.html", await page_context(
        request, title="Profil va limitlar", page_name="Profil", active_page="profile"))


@router.get("/privacy")
async def privacy(request: Request):
    values = context(request, title="Maxfiylik", auth_page=True)
    return templates.TemplateResponse(request, "privacy.html", values)
