import asyncio
import re
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from starlette.datastructures import UploadFile
from starlette.responses import FileResponse, JSONResponse, Response

from app.ai.rules import check_business_rules
from app.errors import AppError, not_found
from app.models import CardImage
from app.schemas import (
    CardContent,
    CardPatch,
    ExportRequest,
    GenerateRequest,
    UzumPublishRequest,
    UzumTemplateRequest,
)
from app.services import cards, exports, jobs, quotas
from app.services.ikpu import recommended, suggest_ikpu
from app.services.image_hosting import publish_images
from app.services.images import MAX_FILE_BYTES, image_path, prepare_image
from app.services.uzum_seller import get_template, list_shops, upload_template
from app.services.uzum_template import fill_template, inspect_template, recommend_category
from app.web import current_user, verify_csrf

router = APIRouter(prefix="/api")


def idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
        raise AppError("idempotency_required", "So'rov kaliti yo'q. Sahifani yangilang.")
    return key


async def read_upload(upload: UploadFile) -> bytes:
    output = bytearray()
    while chunk := await upload.read(1024 * 1024):
        output.extend(chunk)
        if len(output) > MAX_FILE_BYTES:
            raise AppError("image_size", "Har bir rasm 10 MiB dan kichik bo'lishi kerak.", 413)
    return bytes(output)


@router.post("/cards", status_code=201)
async def create(request: Request):
    user = current_user(request, verified=True)
    verify_csrf(request)
    key = idempotency_key(request)
    async with request.form(max_files=6, max_fields=5, max_part_size=1024 * 1024) as form:
        files = form.getlist("images[]")
        if not 1 <= len(files) <= 5 or any(not isinstance(image, UploadFile) for image in files):
            raise AppError("image_count", "1-5 ta mahsulot rasmini tanlang.")
        notes, consent = str(form.get("seller_notes", "")).strip(), str(form.get("consent_version", ""))
        images = [await asyncio.to_thread(prepare_image, await read_upload(image)) for image in files]
    payload = await cards.create_card(request.app.state.db, request.app.state.settings, user.id, notes, consent, key, images)
    return JSONResponse(payload, status_code=201)


@router.get("/cards")
async def history(request: Request, q: str = Query("", max_length=200),
                  status: str = Query("", pattern="^(|draft|ready|queued|running|failed)$"),
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        return await cards.list_cards(session, user.id, q, status, page, page_size)


@router.get("/cards/{card_id}")
async def get_card(request: Request, card_id: UUID):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        return await cards.card_payload(session, await cards.owned_card(session, user.id, card_id))


@router.patch("/cards/{card_id}")
async def update_card(request: Request, card_id: UUID, patch: CardPatch):
    user = current_user(request)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return await cards.save_card(session, user.id, card_id, patch)


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(request: Request, card_id: UUID, expected_version: int = Query(..., ge=1)):
    user = current_user(request)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        await cards.delete_card(session, user.id, card_id, expected_version)
    return Response(status_code=204)


@router.get("/cards/{card_id}/images/{image_id}")
async def get_image(request: Request, card_id: UUID, image_id: UUID):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        await cards.owned_card(session, user.id, card_id)
        image = await session.scalar(select(CardImage).where(CardImage.card_id == card_id, CardImage.id == image_id))
        if not image:
            raise not_found()
        path = image_path(request.app.state.settings.upload_dir, image.storage_key)
    if not path.is_file():
        raise not_found()
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/cards/{card_id}/generations", status_code=202)
async def generate(request: Request, card_id: UUID, payload: GenerateRequest):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        result = await jobs.enqueue(session, user.id, card_id, payload, idempotency_key(request), request.app.state.settings)
    return JSONResponse(result, status_code=202)


@router.get("/jobs/{job_id}")
async def job_status(request: Request, job_id: UUID):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        return cards.job_payload(await jobs.owned_job(session, user.id, job_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: UUID):
    user = current_user(request)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        return await jobs.cancel(session, user.id, job_id)


@router.post("/exports")
async def export(request: Request, payload: ExportRequest):
    user = current_user(request)
    verify_csrf(request)
    async with request.app.state.db.transaction() as session:
        rows = await exports.collect_rows(session, user.id, payload.card_ids)
    writer = exports.make_xlsx if payload.format == "xlsx" else exports.make_csv
    data = await asyncio.to_thread(writer, rows)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if payload.format == "xlsx" else "text/csv; charset=utf-8"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="karto-cards.{payload.format}"', "Cache-Control": "no-store"})


@router.post("/cards/{card_id}/uzum-import-file")
async def export_uzum_template(request: Request, card_id: UUID):
    data, _ = await _prepare_uzum_template(request, card_id, UzumTemplateRequest)
    return Response(data, media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
                    headers={"Content-Disposition": f'attachment; filename="uzum-{card_id}.xlsm"',
                             "Cache-Control": "no-store"})


@router.post("/cards/{card_id}/uzum-publish")
async def publish_uzum_template(request: Request, card_id: UUID):
    data, payload = await _prepare_uzum_template(request, card_id, UzumPublishRequest)
    result = await upload_template(request.app.state.settings, data, payload.shop_id)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def _prepare_uzum_template(request: Request, card_id: UUID, payload_type):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.form(max_files=1, max_fields=2, max_part_size=1024 * 1024) as form:
        template = form.get("template")
        raw_payload = form.get("payload")
        if not isinstance(raw_payload, str):
            raise AppError("uzum_fields_invalid", "Uzum uchun maydonlarni kiriting.")
        template_bytes = (await read_upload(template) if isinstance(template, UploadFile) else b"") or None
        try:
            payload = payload_type.model_validate_json(raw_payload)
        except ValueError:
            raise AppError("uzum_fields_invalid", "Uzum uchun kiritilgan maydonlarni tekshiring.") from None
    async with request.app.state.db.transaction() as session:
        await cards.actor(session, user.id, verified=True)
        card = await cards.owned_card(session, user.id, card_id)
        cards.ensure_version(card, payload.expected_version)
        await cards.ensure_idle(session, card)
        if card.status != "ready" or cards.unresolved_reviews(card):
            raise AppError("review_required", "Uzum faylidan oldin kartochkani tekshirib, saqlang.", 409)
        content = CardContent.model_validate(card.content_json)
        try:
            check_business_rules(content)
        except ValueError:
            raise AppError("export_validation", "Kartochkada Uzum qoidalariga mos kelmagan matn bor.", 409) from None
        values = payload.model_dump()
        photo_urls = payload.photo_urls
        if not photo_urls:
            keys = (await session.scalars(
                select(CardImage.storage_key).where(CardImage.card_id == card.id).order_by(CardImage.position),
            )).all()
            image_paths = [image_path(request.app.state.settings.upload_dir, key) for key in keys]
        values.update({"category_id": str(payload.category_id), "photo_urls": photo_urls,
                       "seller_id": card.id.hex, "sku_group": payload.sku_group or f"K{card.id.hex[:12].upper()}",
                       "content": content.model_dump()})
    if not photo_urls:
        values["photo_urls"] = await publish_images(image_paths)
    if template_bytes is None:
        template_bytes = await get_template(request.app.state.settings)
    data = await asyncio.to_thread(fill_template, template_bytes, values)
    return data, payload


@router.post("/cards/{card_id}/uzum-template/catalog")
async def uzum_template_catalog(request: Request, card_id: UUID):
    user = current_user(request, verified=True)
    verify_csrf(request)
    async with request.form(max_files=1, max_fields=1, max_part_size=1024 * 1024) as form:
        template = form.get("template")
        template_bytes = (await read_upload(template) if isinstance(template, UploadFile) else b"") or None
    async with request.app.state.db.transaction() as session:
        await cards.actor(session, user.id, verified=True)
        card = await cards.owned_card(session, user.id, card_id)
        if card.status != "ready" or cards.unresolved_reviews(card):
            raise AppError("review_required", "Avval kartochkani tekshirib, saqlang.", 409)
        content = CardContent.model_validate(card.content_json).model_dump()
        seller_notes = card.seller_notes
    if template_bytes is None:
        template_bytes = await get_template(request.app.state.settings)
    info = await asyncio.to_thread(inspect_template, template_bytes)
    result = info.to_payload()
    result["recommended_category"] = recommend_category(content, seller_notes, info.categories)
    result["shops"] = await list_shops(request.app.state.settings)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/cards/{card_id}/ikpu")
async def uzum_ikpu(request: Request, card_id: UUID, category: str = Query("", max_length=200),
                    q: str = Query("", max_length=100)):
    user = current_user(request, verified=True)
    async with request.app.state.db.transaction() as session:
        card = await cards.owned_card(session, user.id, card_id)
        content = CardContent.model_validate(card.content_json).model_dump() if card.content_json else {}
    title = ((content.get("title") or {}).get("ru")) or ""
    hint = ((content.get("suggested_category") or {}).get("ru")) or ""
    words = [word for word in re.findall(r"\w{4,}", title)][:3]
    queries = [q] if q.strip() else [category, hint, " ".join(words[:2]), *words]
    candidates = await suggest_ikpu(queries, f"{q} {category} {title} {hint}")
    return JSONResponse({"candidates": candidates, "recommended": None if q.strip() else recommended(candidates)},
                        headers={"Cache-Control": "no-store"})


@router.get("/me/usage")
async def usage(request: Request):
    user = current_user(request)
    async with request.app.state.db.transaction() as session:
        await cards.actor(session, user.id)
        return await quotas.usage(session, user.id, request.app.state.settings)
