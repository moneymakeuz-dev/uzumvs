import logging
import shutil
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.clock import periods, utcnow
from app.config import Settings
from app.db import Database
from app.models import AICallUsage, DailyBudget, GenerationJob, WorkerHeartbeat

logger = logging.getLogger(__name__)
MESSAGES = {
    "worker_heartbeat_stale": "Worker 60 soniyadan beri javob bermayapti.",
    "queue_delayed": "Navbatdagi eng eski vazifa 2 daqiqadan ortiq kutmoqda.",
    "provider_failures": "AI provayderiga oxirgi 5 ta chaqiruv muvaffaqiyatsiz tugadi.",
    "budget_80": "Kunlik AI budjetining 80% ishlatildi.",
    "budget_exhausted": "Kunlik AI budjeti tugadi.",
    "disk_80": "Rasm diskining 80% band.",
    "disk_90": "Rasm diskining 90% band. Yangi yuklashlar to'xtatiladi.",
}


async def collect_alerts(db: Database, settings: Settings) -> list[str]:
    alerts: list[str] = []
    async with db.transaction() as session:
        heartbeat = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
        if heartbeat is None or heartbeat < utcnow() - timedelta(seconds=60):
            alerts.append("worker_heartbeat_stale")
        oldest = await session.scalar(select(func.min(GenerationJob.created_at)).where(GenerationJob.status == "queued"))
        if oldest and oldest < utcnow() - timedelta(minutes=2):
            alerts.append("queue_delayed")
        outcomes = (await session.scalars(select(AICallUsage.outcome).where(AICallUsage.outcome != "not_called")
                                          .order_by(AICallUsage.started_at.desc()).limit(5))).all()
        if len(outcomes) == 5 and all(outcome == "unknown" for outcome in outcomes):
            alerts.append("provider_failures")
        budget = await session.get(DailyBudget, periods()[1])
        if budget and budget.limit_usd > 0:
            used = (budget.spent_usd + budget.reserved_usd) / budget.limit_usd
            alerts += ["budget_exhausted"] if used >= 1 else ["budget_80"] if used >= 0.8 else []
    alerts += disk_alerts(settings)
    return alerts


def disk_alerts(settings: Settings) -> list[str]:
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(settings.upload_dir)
    except OSError:
        return ["disk_90"]
    ratio = usage.used / usage.total
    return ["disk_90"] if ratio >= 0.9 else ["disk_80"] if ratio >= 0.8 else []


async def worker_alive(db: Database) -> bool:
    async with db.transaction() as session:
        heartbeat = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    return bool(heartbeat and heartbeat >= utcnow() - timedelta(seconds=60))


async def send_alerts(settings: Settings, alerts: list[str]) -> bool:
    webhook = settings.alert_webhook_url.get_secret_value()
    lines = [f"- {MESSAGES.get(code, code)}" for code in alerts]
    logger.warning("operational_alerts codes=%s", ",".join(alerts))
    if not webhook:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook, json={"text": "Karto ogohlantirish:\n" + "\n".join(lines), "codes": alerts})
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.error("alert_delivery_failed")
        return False
