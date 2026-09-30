from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import PROMPT_VERSION
from app.ai.rules import RULES, RULES_VERSION
from app.clock import utcnow
from app.config import Settings
from app.db import Database, advisory_lock
from app.errors import AppError, not_found
from app.models import Card, CardImage, GenerationJob
from app.schemas import CardContent, GenerateRequest
from app.security import fingerprint
from app.services import budget, quotas
from app.services.cards import ACTIVE, actor, ensure_idle, ensure_version, job_payload, owned_card


async def owned_job(session: AsyncSession, user_id: UUID, job_id: UUID) -> GenerationJob:
    job = await session.scalar(select(GenerationJob).join(Card).where(
        GenerationJob.id == job_id, GenerationJob.user_id == user_id, Card.deleted_at.is_(None),
    ))
    if not job:
        raise not_found()
    return job


async def enqueue(session: AsyncSession, user_id: UUID, card_id: UUID, request: GenerateRequest,
                  key: str, settings: Settings) -> dict[str, Any]:
    await advisory_lock(session, "generation-queue")
    user = await actor(session, user_id, verified=True)
    digest = fingerprint({"card_id": str(card_id), **request.model_dump()})
    existing = await session.scalar(select(GenerationJob).where(
        GenerationJob.user_id == user_id, GenerationJob.idempotency_key == key,
    ))
    if existing:
        if existing.request_fingerprint != digest:
            raise AppError("idempotency_conflict", "So'rov kaliti boshqa amal uchun ishlatilgan.", 409)
        return job_payload(existing)
    card = await owned_card(session, user_id, card_id)
    ensure_version(card, request.expected_version)
    await ensure_idle(session, card)
    await validate_enqueue(session, user, card, request, settings)
    bucket = "initial" if request.operation == "initial" else "regeneration"
    period = await quotas.reserve(session, user_id, bucket, settings)
    images = (await session.scalars(select(CardImage).where(CardImage.card_id == card.id)
                                   .order_by(CardImage.position))).all()
    snapshot = {"seller_notes": card.seller_notes, "content": card.content_json,
                "field_path": request.field_path, "operation": request.operation,
                "provider": settings.ai_provider, "rules": RULES,
                "images": [{"storage_key": image.storage_key} for image in images]}
    job = GenerationJob(user_id=user_id, card_id=card.id, operation=request.operation,
                        field_path=request.field_path, base_version=card.version, input_snapshot_json=snapshot,
                        idempotency_key=key, request_fingerprint=digest, provider_model=settings.ai_model,
                        prompt_version=PROMPT_VERSION, rules_version=RULES_VERSION,
                        quota_period=period, quota_bucket=bucket)
    session.add(job)
    await session.flush()
    return {**job_payload(job), "usage": await quotas.usage(session, user_id, settings)}


async def validate_enqueue(session: AsyncSession, user: Any, card: Card, request: GenerateRequest,
                           settings: Settings) -> None:
    if settings.ai_provider == "disabled":
        raise AppError("ai_not_configured", "AI xizmati hali ulanmagan. Administrator bilan bog'laning.", 503)
    if user.consent_version != settings.consent_version or not user.ai_consent_at:
        raise AppError("consent_required", "AI xizmatiga yuborish uchun yangilangan rozilik kerak.", 403)
    if (request.operation == "initial") != (card.status == "draft"):
        raise AppError("invalid_operation", "Kartochka holati bu amalga mos emas.", 409)
    active = await session.scalar(select(GenerationJob.id).where(
        GenerationJob.user_id == user.id, GenerationJob.status.in_(ACTIVE),
    ))
    if active:
        raise AppError("job_active", "Avvalgi vazifa tugashini kuting.", 409)
    queued = await session.scalar(select(func.count(GenerationJob.id)).where(GenerationJob.status.in_(ACTIVE)))
    if (queued or 0) >= settings.max_queue_size:
        raise AppError("queue_full", "Navbat to'lgan. Birozdan keyin qayta urinib ko'ring.", 503, retry_after=30)
    await budget.ensure_budget(session, settings)


async def cancel(session: AsyncSession, user_id: UUID, job_id: UUID) -> dict[str, Any]:
    await advisory_lock(session, "generation-queue")
    await actor(session, user_id)
    job = await owned_job(session, user_id, job_id)
    if job.status == "cancelled":
        return job_payload(job)
    if job.status != "queued":
        raise AppError("cannot_cancel", "Faqat navbatdagi vazifani bekor qilish mumkin.", 409)
    await terminate(session, job, "cancelled", None)
    return job_payload(job)


async def terminate(session: AsyncSession, job: GenerationJob, status: str, error_code: str | None) -> None:
    if job.status not in ACTIVE:
        return
    await quotas.settle(session, job, succeeded=False)
    job.status, job.error_code = status, error_code
    job.finished_at, job.lease_expires_at = utcnow(), None


async def claim(db: Database, worker_id: str) -> GenerationJob | None:
    async with db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        job = await session.scalar(select(GenerationJob).where(GenerationJob.status == "queued")
                                   .order_by(GenerationJob.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        await advisory_lock(session, f"user:{job.user_id}")
        job.status, job.worker_id = "running", worker_id
        job.started_at, job.heartbeat_at = utcnow(), utcnow()
        job.lease_expires_at = utcnow() + timedelta(seconds=180)
        return job


async def complete(db: Database, job_id: UUID, worker_id: str, content: CardContent,
                   original_result: dict) -> bool:
    async with db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        job = await session.get(GenerationJob, job_id)
        if not job or job.status != "running" or job.worker_id != worker_id:
            return False
        await advisory_lock(session, f"user:{job.user_id}")
        if not job.lease_expires_at or job.lease_expires_at <= utcnow():
            await terminate(session, job, "failed", "worker_interrupted")
            return False
        card = await session.get(Card, job.card_id, with_for_update=True)
        if not card or card.deleted_at or card.version != job.base_version:
            await terminate(session, job, "failed", "version_conflict")
            return False
        card.content_json = content.model_dump()
        card.last_ai_json = original_result
        if not job.field_path:
            card.review_resolutions_json = {}
        card.status, card.updated_at = "ready", utcnow()
        card.version += 1
        job.result_json = content.model_dump()
        job.status, job.finished_at, job.lease_expires_at = "succeeded", utcnow(), None
        await quotas.settle(session, job, succeeded=True)
        return True


async def fail(db: Database, settings: Settings, job_id: UUID, code: str) -> None:
    async with db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        job = await session.get(GenerationJob, job_id)
        if not job or job.status not in ACTIVE:
            return
        await advisory_lock(session, f"user:{job.user_id}")
        await budget.settle_interrupted_calls(session, job, settings)
        await terminate(session, job, "failed", code)


async def recover(db: Database, settings: Settings) -> int:
    async with db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        stale = (await session.scalars(select(GenerationJob).where(or_(
            (GenerationJob.status == "queued") & (GenerationJob.created_at < utcnow() - timedelta(minutes=10)),
            (GenerationJob.status == "running") & (GenerationJob.lease_expires_at < utcnow()),
        )))).all()
        for job in stale:
            await advisory_lock(session, f"user:{job.user_id}")
            await budget.settle_interrupted_calls(session, job, settings)
            code = "queue_expired" if job.status == "queued" else "worker_interrupted"
            await terminate(session, job, "failed", code)
        return len(stale)
