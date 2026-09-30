from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import ProviderResult
from app.clock import periods, utcnow
from app.config import Settings
from app.db import advisory_lock
from app.errors import AppError
from app.models import AICallUsage, DailyBudget, GenerationJob


def maximum_call_cost(settings: Settings) -> Decimal:
    if settings.ai_provider == "mock":
        return Decimal("0")
    return ((settings.ai_max_input_tokens * settings.ai_input_price_per_million
             + settings.ai_max_output_tokens * settings.ai_output_price_per_million)
            / Decimal("1000000")).quantize(Decimal("0.00000001"))


async def current_budget(session: AsyncSession, settings: Settings) -> DailyBudget:
    _, day = periods()
    await advisory_lock(session, f"budget:{day}")
    budget = await session.get(DailyBudget, day, with_for_update=True)
    if budget is None:
        budget = DailyBudget(day=day, limit_usd=settings.ai_daily_budget_usd)
        session.add(budget)
        await session.flush()
    budget.limit_usd = settings.ai_daily_budget_usd
    return budget


async def ensure_budget(session: AsyncSession, settings: Settings) -> DailyBudget:
    budget = await current_budget(session, settings)
    if settings.ai_provider == "mock":
        return budget
    if budget.spent_usd + budget.reserved_usd + maximum_call_cost(settings) > budget.limit_usd:
        raise AppError("budget_exhausted", "AI xizmatining bugungi budjeti tugagan.", 503, retry_after=3600)
    return budget


async def reserve_call(session: AsyncSession, job: GenerationJob, settings: Settings) -> AICallUsage:
    budget = await ensure_budget(session, settings)
    cost = maximum_call_cost(settings)
    budget.reserved_usd += cost
    job.attempt_count += 1
    usage = AICallUsage(job_id=job.id, attempt_number=job.attempt_count, model=job.provider_model,
                        budget_day=budget.day, reserved_cost=cost, price_version=settings.ai_price_version)
    session.add(usage)
    await session.flush()
    return usage


async def settle_call(session: AsyncSession, call_id: UUID, settings: Settings,
                      result: ProviderResult | None = None, called: bool = True) -> None:
    usage = await session.get(AICallUsage, call_id, with_for_update=True)
    if not usage or usage.budget_state != "reserved":
        return
    await advisory_lock(session, f"budget:{usage.budget_day}")
    budget = await session.get(DailyBudget, usage.budget_day, with_for_update=True)
    if budget is None:
        raise RuntimeError("Missing provider cost reservation")
    cost = usage.reserved_cost if called else Decimal("0")
    usage.cost_basis = "upper_bound" if called else "not_called"
    if result and result.input_tokens is not None and result.output_tokens is not None:
        tokens = max(result.output_tokens, 0) + max(result.thinking_tokens or 0, 0)
        cost = (max(result.input_tokens, 0) * settings.ai_input_price_per_million
                + tokens * settings.ai_output_price_per_million) / Decimal("1000000")
        usage.input_tokens, usage.output_tokens = result.input_tokens, result.output_tokens
        usage.thinking_tokens, usage.provider_request_id = result.thinking_tokens, result.request_id
        usage.cost_basis = "reported"
    if settings.ai_provider == "mock":
        cost, usage.cost_basis = Decimal("0"), "not_called"
    budget.reserved_usd -= usage.reserved_cost
    budget.spent_usd += cost.quantize(Decimal("0.00000001"))
    usage.estimated_cost = cost
    usage.outcome = "returned" if result else ("unknown" if called else "not_called")
    usage.budget_state, usage.finished_at = "settled", utcnow()


async def settle_interrupted_calls(session: AsyncSession, job: GenerationJob, settings: Settings) -> None:
    calls = (await session.scalars(select(AICallUsage.id).where(
        AICallUsage.job_id == job.id, AICallUsage.budget_state == "reserved",
    ))).all()
    for call_id in calls:
        await settle_call(session, call_id, settings)
