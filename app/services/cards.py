import asyncio
import copy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rules import rule_warnings
from app.clock import utcnow
from app.config import Settings
from app.db import Database, advisory_lock
from app.errors import AppError, not_found
from app.models import Card, CardImage, DeletionEvent, GenerationJob, User
from app.schemas import CardContent, CardPatch, value_at
from app.security import fingerprint
from app.services.images import PreparedImage, image_path, write_image
from app.services.quotas import rows

ACTIVE = ("queued", "running")


async def actor(session: AsyncSession, user_id: UUID, verified: bool = False) -> User:
    await advisory_lock(session, f"user:{user_id}")
    user = await session.get(User, user_id, populate_existing=True)
    if not user or user.deletion_requested_at:
        raise AppError("unauthorized", "Hisobingizga qayta kiring.", 401)
    if verified and not user.email_verified_at:
        raise AppError("email_unverified", "Avval email manzilingizni tasdiqlang.", 403)
    return user


async def owned_card(session: AsyncSession, user_id: UUID, card_id: UUID) -> Card:
    card = await session.scalar(select(Card).join(User).where(
        Card.id == card_id, Card.user_id == user_id, Card.deleted_at.is_(None),
        User.deletion_requested_at.is_(None),
    ))
    if not card:
        raise not_found()
    return card


async def ensure_idle(session: AsyncSession, card: Card) -> None:
    active = await session.scalar(select(GenerationJob.id).where(
        GenerationJob.card_id == card.id, GenerationJob.status.in_(ACTIVE),
    ))
    if active:
        raise AppError("job_active", "Vazifa tugashini kuting.", 409)


def ensure_version(card: Card, expected: int) -> None:
    if card.version != expected:
        raise AppError("version_conflict", "Kartochka boshqa oynada o'zgargan. Yangi versiyani ko'ring.",
                       409, {"current_version": card.version})


def unresolved_reviews(card: Card) -> list[dict[str, Any]]:
    if not card.content_json:
        return []
    return [item for item in card.content_json["review_items"]
            if card.review_resolutions_json.get(item["id"], {}).get("value_hash")
            != fingerprint(value_at(card.content_json, item["path"]))]


async def card_payload(session: AsyncSession, card: Card) -> dict[str, Any]:
    images = (await session.scalars(select(CardImage).where(CardImage.card_id == card.id)
                                   .order_by(CardImage.position))).all()
    job = await session.scalar(select(GenerationJob).where(GenerationJob.card_id == card.id)
                               .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc()).limit(1))
    return {
        "id": str(card.id), "version": card.version, "status": card.status,
        "seller_notes": card.seller_notes, "content": card.content_json,
        "unresolved_reviews": unresolved_reviews(card),
        "rule_warnings": rule_warnings(CardContent.model_validate(card.content_json)) if card.content_json else [],
        "images": [{"id": str(image.id), "url": f"/api/cards/{card.id}/images/{image.id}",
                    "width": image.width, "height": image.height, "position": image.position} for image in images],
        "latest_job": job_payload(job) if job else None,
        "created_at": card.created_at.isoformat(), "updated_at": card.updated_at.isoformat(),
    }


def job_payload(job: GenerationJob) -> dict[str, Any]:
    return {"id": str(job.id), "job_id": str(job.id), "card_id": str(job.card_id), "status": job.status,
            "operation": job.operation, "field_path": job.field_path, "error_code": job.error_code,
            "status_url": f"/api/jobs/{job.id}", "result_version": job.base_version + 1 if job.status == "succeeded" else None}


async def create_card(db: Database, settings: Settings, user_id: UUID, notes: str,
                      consent: str, key: str, images: list[PreparedImage]) -> dict[str, Any]:
    if not 1 <= len(images) <= 5 or len(notes) > 2000:
        raise AppError("invalid_upload", "1-5 ta rasm va 2000 belgigacha izoh kiriting.")
    if consent != settings.consent_version:
        raise AppError("consent_required", "Rasmlarni AI xizmatiga yuborishga rozilik kerak.", 403)
    digest = fingerprint({"notes": notes, "images": [image.sha256 for image in images], "consent": consent})
    written: list[str] = []
    try:
        async with db.transaction() as session:
            user = await actor(session, user_id, verified=True)
            existing = await session.scalar(select(Card).where(Card.user_id == user_id, Card.creation_key == key))
            if existing:
                if existing.creation_fingerprint != digest or existing.deleted_at:
                    raise AppError("idempotency_conflict", "Bu so'rov kaliti oldin boshqa amal uchun ishlatilgan.", 409)
                return await card_payload(session, existing)
            _, daily = await rows(session, user_id, settings)
            if daily.created_drafts >= settings.daily_draft_limit:
                raise AppError("draft_limit", "Bugungi yangi kartochkalar limiti tugagan.", 429, retry_after=3600)
            card = Card(id=uuid4(), user_id=user_id, seller_notes=notes,
                        creation_key=key, creation_fingerprint=digest)
            session.add(card)
            await session.flush()
            for position, image in enumerate(images):
                storage_key = await asyncio.to_thread(write_image, settings.upload_dir, image)
                written.append(storage_key)
                session.add(CardImage(card_id=card.id, storage_key=storage_key, sha256=image.sha256,
                                      width=image.width, height=image.height, size_bytes=len(image.data), position=position))
            user.ai_consent_at, user.consent_version = utcnow(), consent
            daily.created_drafts += 1
            await session.flush()
            return await card_payload(session, card)
    except (Exception, asyncio.CancelledError):
        for storage_key in written:
            image_path(settings.upload_dir, storage_key).unlink(missing_ok=True)
        raise


def merge_edits(original: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    allowed = set(CardContent.model_fields) - {"review_items", "schema_version"}
    if set(edited) - allowed:
        raise AppError("protected_metadata", "Tekshirish metadata'sini o'zgartirib bo'lmaydi.")
    content = copy.deepcopy(original)
    if "attributes" in edited:
        old_attributes = {attribute["key"]: attribute for attribute in original["attributes"]}
        attributes = []
        if not isinstance(edited["attributes"], list) or len(edited["attributes"]) > 30:
            raise AppError("invalid_attributes", "Xususiyatlar ro'yxatini tekshiring.")
        for attribute in edited["attributes"]:
            if not isinstance(attribute, dict) or set(attribute) != {"key", "name", "value"}:
                raise AppError("invalid_attributes", "Xususiyat nomi va qiymatini tekshiring.")
            previous = old_attributes.get(attribute["key"], {})
            unchanged = all(previous.get(field) == attribute[field] for field in ("name", "value"))
            attributes.append({**attribute, "source": previous.get("source", "seller") if unchanged else "seller"})
        edited = {**edited, "attributes": attributes}
    content.update(edited)
    try:
        return CardContent.model_validate(content).model_dump()
    except (ValidationError, TypeError, KeyError):
        raise AppError("invalid_content", "Kartochka maydonlarini tekshiring.") from None


async def save_card(session: AsyncSession, user_id: UUID, card_id: UUID, patch: CardPatch) -> dict:
    await actor(session, user_id)
    card = await owned_card(session, user_id, card_id)
    ensure_version(card, patch.expected_version)
    await ensure_idle(session, card)
    if patch.content is not None:
        if not card.content_json:
            raise AppError("draft_content", "Avval kartochka yarating.", 409)
        card.content_json = merge_edits(card.content_json, patch.content)
    if patch.seller_notes is not None:
        card.seller_notes = patch.seller_notes
    if card.content_json:
        resolve_reviews(card, patch.resolve_review_ids, user_id)
    elif patch.resolve_review_ids:
        raise AppError("invalid_review", "Tekshirish maydoni mavjud emas.")
    card.version += 1
    card.updated_at = utcnow()
    await session.flush()
    return await card_payload(session, card)


def resolve_reviews(card: Card, requested: list[str], user_id: UUID) -> None:
    reviews = {item["id"]: item for item in card.content_json["review_items"]}
    if set(requested) - set(reviews):
        raise AppError("invalid_review", "Tekshirish maydoni mavjud emas.")
    resolutions = {key: value for key, value in card.review_resolutions_json.items()
                   if key in reviews and value.get("value_hash") == fingerprint(value_at(card.content_json, reviews[key]["path"]))}
    for key in requested:
        resolutions[key] = {"user_id": str(user_id), "resolved_at": utcnow().isoformat(),
                            "value_hash": fingerprint(value_at(card.content_json, reviews[key]["path"]))}
    card.review_resolutions_json = resolutions


async def delete_card(session: AsyncSession, user_id: UUID, card_id: UUID, version: int) -> None:
    await actor(session, user_id)
    card = await owned_card(session, user_id, card_id)
    ensure_version(card, version)
    await ensure_idle(session, card)
    card.deleted_at = utcnow()
    session.add(DeletionEvent(entity_type="card", entity_id=card.id, expires_at=utcnow() + timedelta(days=31)))


async def list_cards(session: AsyncSession, user_id: UUID, query: str = "", status: str = "",
                     page: int = 1, page_size: int = 20) -> dict:
    filters = [Card.user_id == user_id, Card.deleted_at.is_(None)]
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filters.append(or_(Card.seller_notes.ilike(pattern), Card.content_json["title"]["uz"].astext.ilike(pattern),
                           Card.content_json["title"]["ru"].astext.ilike(pattern)))
    if status in {"draft", "ready"}:
        filters.append(Card.status == status)
    elif status in {"queued", "running", "failed"}:
        latest = select(GenerationJob.status).where(GenerationJob.card_id == Card.id).order_by(
            GenerationJob.created_at.desc(), GenerationJob.id.desc()).limit(1).scalar_subquery()
        filters.append(latest == status)
    total = await session.scalar(select(func.count(Card.id)).where(*filters))
    cards = (await session.scalars(select(Card).where(*filters).order_by(Card.updated_at.desc(), Card.id.desc())
                                   .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [await card_payload(session, card) for card in cards],
            "total": total or 0, "page": page, "page_size": page_size}
