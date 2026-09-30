from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import periods, reset_times
from app.config import Settings
from app.db import advisory_lock
from app.errors import AppError
from app.models import DailyUsage, GenerationJob, MonthlyUsage


async def rows(session: AsyncSession, user_id: UUID, settings: Settings) -> tuple[MonthlyUsage, DailyUsage]:
    await advisory_lock(session, f"user:{user_id}")
    month, day = periods()
    monthly = await session.get(MonthlyUsage, (user_id, month))
    daily = await session.get(DailyUsage, (user_id, day))
    if monthly is None:
        monthly = MonthlyUsage(user_id=user_id, period=month, initial_limit=settings.initial_monthly_limit,
                               regeneration_limit=settings.regeneration_monthly_limit)
        session.add(monthly)
    if daily is None:
        daily = DailyUsage(user_id=user_id, day=day)
        session.add(daily)
    await session.flush()
    return monthly, daily


async def reserve(session: AsyncSession, user_id: UUID, bucket: str, settings: Settings) -> str:
    monthly, daily = await rows(session, user_id, settings)
    available = getattr(monthly, f"{bucket}_limit") - getattr(monthly, f"{bucket}_used") - getattr(monthly, f"{bucket}_reserved")
    if available <= 0:
        raise AppError("quota_exceeded", "Bu oy uchun limit tugagan.", 429, reset_times(), 3600)
    if daily.accepted_jobs >= settings.daily_job_limit:
        raise AppError("daily_quota_exceeded", "Bugungi AI amallari limiti tugagan.", 429, reset_times(), 3600)
    setattr(monthly, f"{bucket}_reserved", getattr(monthly, f"{bucket}_reserved") + 1)
    daily.accepted_jobs += 1
    return monthly.period


async def settle(session: AsyncSession, job: GenerationJob, succeeded: bool) -> None:
    if job.quota_state != "reserved":
        return
    await advisory_lock(session, f"user:{job.user_id}")
    monthly = await session.get(MonthlyUsage, (job.user_id, job.quota_period), with_for_update=True)
    if monthly is None:
        raise RuntimeError("Missing quota reservation")
    reserved = f"{job.quota_bucket}_reserved"
    used = f"{job.quota_bucket}_used"
    setattr(monthly, reserved, getattr(monthly, reserved) - 1)
    if succeeded:
        setattr(monthly, used, getattr(monthly, used) + 1)
    job.quota_state = "consumed" if succeeded else "released"


async def usage(session: AsyncSession, user_id: UUID, settings: Settings) -> dict:
    monthly, daily = await rows(session, user_id, settings)
    result = {"period": monthly.period, "daily_remaining": max(0, settings.daily_job_limit - daily.accepted_jobs),
              "drafts_remaining": max(0, settings.daily_draft_limit - daily.created_drafts), **reset_times()}
    for bucket in ("initial", "regeneration"):
        values = {name: getattr(monthly, f"{bucket}_{name}") for name in ("limit", "used", "reserved")}
        values["available"] = values["limit"] - values["used"] - values["reserved"]
        result[bucket] = values
    return result
