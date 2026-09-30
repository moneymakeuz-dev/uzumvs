from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import clock
from app.errors import AppError
from app.models import DailyUsage, MonthlyUsage
from app.services import quotas


def test_periods_follow_tashkent_midnight():
    assert clock.periods(datetime(2026, 9, 30, 18, 59, 59, tzinfo=UTC)) == ("2026-09", "2026-09-30")
    assert clock.periods(datetime(2026, 9, 30, 19, 0, tzinfo=UTC)) == ("2026-10", "2026-10-01")


@pytest.mark.integration
async def test_daily_and_monthly_counters_switch_at_tashkent_midnight(database, account, monkeypatch):
    db, settings = database
    settings.daily_job_limit = 1
    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 9, 30, 18, 59, 59, tzinfo=UTC))
    async with db.transaction() as session:
        assert await quotas.reserve(session, account.id, "initial", settings) == "2026-09"
    with pytest.raises(AppError) as error:
        async with db.transaction() as session:
            await quotas.reserve(session, account.id, "regeneration", settings)
    assert error.value.code == "daily_quota_exceeded" and error.value.status == 429
    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 9, 30, 19, 0, tzinfo=UTC))
    async with db.transaction() as session:
        assert await quotas.reserve(session, account.id, "initial", settings) == "2026-10"
        monthly = {row.period: row.initial_reserved for row in (await session.scalars(select(MonthlyUsage))).all()}
        daily = {row.day: row.accepted_jobs for row in (await session.scalars(select(DailyUsage))).all()}
    assert monthly == {"2026-09": 1, "2026-10": 1}
    assert daily == {"2026-09-30": 1, "2026-10-01": 1}


@pytest.mark.integration
async def test_monthly_limits_are_separate_and_exact(database, account):
    db, settings = database
    settings.initial_monthly_limit, settings.regeneration_monthly_limit, settings.daily_job_limit = 2, 1, 10
    async with db.transaction() as session:
        for _ in range(2):
            await quotas.reserve(session, account.id, "initial", settings)
        await quotas.reserve(session, account.id, "regeneration", settings)
    for bucket in ("initial", "regeneration"):
        with pytest.raises(AppError) as error:
            async with db.transaction() as session:
                await quotas.reserve(session, account.id, bucket, settings)
        assert error.value.code == "quota_exceeded"
