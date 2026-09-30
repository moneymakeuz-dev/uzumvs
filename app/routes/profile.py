import asyncio
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select
from starlette.responses import Response

from app.clock import utcnow
from app.db import advisory_lock
from app.errors import AppError
from app.models import Card, DeletionEvent, GenerationJob
from app.schemas import StrictModel
from app.security import check_password, hash_password
from app.services import auth, cards, jobs
from app.web import clear_cookie, current_user, verify_csrf

router = APIRouter(prefix="/api/me")


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AccountDeletion(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    confirm: Literal[True]


@router.post("/password", status_code=204)
async def change_password(request: Request, payload: PasswordChange):
    user = current_user(request)
    verify_csrf(request)
    request.app.state.rates.hit("5 per 15 minutes", "password-change", str(user.id))
    async with request.app.state.db.transaction() as session:
        user = await cards.actor(session, user.id)
        if not await asyncio.to_thread(check_password, payload.current_password, user.password_hash):
            raise AppError("invalid_password", "Joriy parol noto'g'ri.", 403)
        user.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
        await auth.revoke_all(session, user.id)
    response = Response(status_code=204)
    clear_cookie(response, request.app.state.settings)
    return response


@router.delete("", status_code=202)
async def delete_account(request: Request, payload: AccountDeletion):
    user = current_user(request)
    verify_csrf(request)
    request.app.state.rates.hit("5 per 15 minutes", "account-delete", str(user.id))
    async with request.app.state.db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        user = await cards.actor(session, user.id)
        if not await asyncio.to_thread(check_password, payload.current_password, user.password_hash):
            raise AppError("invalid_password", "Joriy parol noto'g'ri.", 403)
        active = (await session.scalars(select(GenerationJob).where(
            GenerationJob.user_id == user.id, GenerationJob.status.in_(cards.ACTIVE),
        ))).all()
        if any(job.status == "running" for job in active):
            raise AppError("job_active", "Vazifa tugagach akkauntni o'chirish mumkin.", 409)
        for job in active:
            await jobs.terminate(session, job, "cancelled", None)
        user.deletion_requested_at = utcnow()
        await auth.revoke_all(session, user.id)
        owned = (await session.scalars(select(Card).where(Card.user_id == user.id))).all()
        for card in owned:
            card.deleted_at = card.deleted_at or utcnow()
        session.add(DeletionEvent(entity_type="user", entity_id=user.id, expires_at=utcnow() + timedelta(days=31)))
    response = Response(status_code=202)
    clear_cookie(response, request.app.state.settings)
    return response
