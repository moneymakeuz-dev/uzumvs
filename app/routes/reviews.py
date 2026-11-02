from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from starlette.responses import JSONResponse, Response

from app.errors import AppError
from app.routes.pages import page_context
from app.schemas import (
    ReviewDraftRequest,
    ReviewReplyRequest,
    ReviewSettingsRequest,
    ReviewTemplateRequest,
)
from app.services import uzum_accounts, uzum_reviews
from app.web import context, current_user, templates, verify_csrf

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}
ReviewId = Annotated[int, Path(gt=0, le=2**63 - 1)]


@router.get("/reviews")
async def reviews_page(request: Request):
    user = current_user(request, verified=True)
    async with request.app.state.db.transaction() as session:
        current = uzum_reviews.settings_view(await uzum_reviews.ensure_settings(session, user.id))
        items = [uzum_reviews.template_view(item) for item in await uzum_reviews.templates_of(session, user.id)]
        replies = await uzum_reviews.history(session, user.id)
        account = uzum_accounts.account_view(await uzum_accounts.find(session, user.id))
        shops = uzum_reviews.shops_view(account["shops"], await uzum_reviews.shop_rules(session, user.id))
    minutes = request.app.state.settings.review_reply_interval_seconds // 60
    return templates.TemplateResponse(request, "reviews.html", await page_context(
        request, title="Sharhlarga javob", page_name="Sharhlarga javob", active_page="reviews", account=account,
        review_settings=current, review_shops=shops, method_labels=uzum_reviews.AUTO_METHOD_LABELS,
        review_templates=items, replies=replies, interval_label=f"{minutes} daqiqa"), headers=NO_STORE)


@router.get("/reviews/pending")
async def pending(request: Request, page: int = Query(0, ge=0, le=50)):
    """Javobsiz sharhlar bo'limi (htmx): Uzum xatosi sahifani buzmaydi, bo'lim ichida ko'rsatiladi."""
    user = current_user(request, verified=True)
    try:
        result = await uzum_reviews.list_pending(request.app.state.db, request.app.state.settings, user.id, page)
        error = ""
    except AppError as failure:
        result, error = {"reviews": [], "templates": [], "page": page, "has_more": False}, failure.message
    return templates.TemplateResponse(request, "partials/review_pending.html", context(
        request, result=result, error=error, max_chars=uzum_reviews.MAX_REPLY_CHARS), headers=NO_STORE)


@router.put("/api/review-settings")
async def save_settings(request: Request, payload: ReviewSettingsRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        result = await uzum_reviews.update_settings(session, user.id, payload)
    if payload.auto_reply:
        request.app.state.review_wake.set()
    return JSONResponse(result, headers=NO_STORE)


@router.post("/api/reviews/run", status_code=202)
async def run_now(request: Request):
    current_user(request, verified=True)
    verify_csrf(request)
    request.app.state.review_wake.set()
    return JSONResponse({"status": "scheduled"}, status_code=202)


@router.post("/api/review-templates", status_code=201)
async def create_template(request: Request, payload: ReviewTemplateRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return JSONResponse(await uzum_reviews.create_template(session, user.id, payload), status_code=201)


@router.put("/api/review-templates/{template_id}")
async def update_template(request: Request, template_id: UUID, payload: ReviewTemplateRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return await uzum_reviews.update_template(session, user.id, template_id, payload)


@router.delete("/api/review-templates/{template_id}", status_code=204)
async def delete_template(request: Request, template_id: UUID):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        await uzum_reviews.delete_template(session, user.id, template_id)
    return Response(status_code=204)


@router.post("/api/reviews/{review_id}/draft")
async def draft(request: Request, review_id: ReviewId, payload: ReviewDraftRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    request.app.state.rates.hit("30 per minute", "review-draft", str(user.id))
    result = await uzum_reviews.draft(request.app.state.db, request.app.state.settings, user.id, review_id,
                                      payload.method, payload.template_id)
    return JSONResponse(result, headers=NO_STORE)


@router.post("/api/reviews/{review_id}/reply")
async def reply(request: Request, review_id: ReviewId, payload: ReviewReplyRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    request.app.state.rates.hit("30 per minute", "review-reply", str(user.id))
    result = await uzum_reviews.send(request.app.state.db, request.app.state.settings, user.id, review_id, payload.text)
    return JSONResponse(result, headers=NO_STORE)
