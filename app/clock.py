from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TASHKENT = ZoneInfo("Asia/Tashkent")


def utcnow() -> datetime:
    return datetime.now(UTC)


def periods(moment: datetime | None = None) -> tuple[str, str]:
    local = (moment or utcnow()).astimezone(TASHKENT)
    return local.strftime("%Y-%m"), local.date().isoformat()


def reset_times(moment: datetime | None = None) -> dict[str, str]:
    local = (moment or utcnow()).astimezone(TASHKENT)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_month = (local.replace(day=1) + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    )
    return {"daily_reset_at": tomorrow.isoformat(), "monthly_reset_at": next_month.isoformat()}
