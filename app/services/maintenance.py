import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import Settings
from app.db import Database, advisory_lock
from app.models import (
    AICallUsage,
    AuthToken,
    Card,
    CardImage,
    DailyBudget,
    DeletionEvent,
    GenerationJob,
    LoginSession,
    MonthlyUsage,
    User,
)

logger = logging.getLogger(__name__)
STORAGE_KEY = re.compile(r"^[0-9a-f]{32}\.jpg$")
TERMINAL = ("succeeded", "failed", "cancelled")


@dataclass
class MaintenanceReport:
    dry_run: bool
    expired_sessions: int = 0
    expired_tokens: int = 0
    expired_drafts: int = 0
    purged_cards: int = 0
    purged_users: int = 0
    removed_files: int = 0
    cleared_payloads: int = 0
    deleted_jobs: int = 0
    orphan_files: int = 0
    expired_registry: int = 0


def audit(settings: Settings, action: str, details: dict[str, Any]) -> None:
    settings.audit_log.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": utcnow().isoformat(), "action": action, **details}
    with settings.audit_log.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


async def count(session: AsyncSession, statement: Select) -> int:
    return await session.scalar(select(func.count()).select_from(statement.subquery())) or 0


async def expire_credentials(session: AsyncSession, report: MaintenanceReport) -> None:
    cutoff = utcnow() - timedelta(days=1)
    stale_sessions = or_(LoginSession.expires_at < cutoff, LoginSession.revoked_at < cutoff,
                         LoginSession.last_seen_at < utcnow() - timedelta(days=2))
    stale_tokens = or_(AuthToken.expires_at < cutoff, AuthToken.used_at < cutoff)
    report.expired_sessions = await count(session, select(LoginSession.id).where(stale_sessions))
    report.expired_tokens = await count(session, select(AuthToken.id).where(stale_tokens))
    if not report.dry_run:
        await session.execute(delete(LoginSession).where(stale_sessions))
        await session.execute(delete(AuthToken).where(stale_tokens))


async def expire_drafts(session: AsyncSession, report: MaintenanceReport) -> None:
    active = select(GenerationJob.id).where(GenerationJob.card_id == Card.id, GenerationJob.status.in_(("queued", "running")))
    drafts = (await session.scalars(select(Card).where(
        Card.status == "draft", Card.deleted_at.is_(None), Card.updated_at < utcnow() - timedelta(days=7), ~active.exists(),
    ))).all()
    report.expired_drafts = len(drafts)
    if report.dry_run:
        return
    for card in drafts:
        card.deleted_at = utcnow()
        session.add(DeletionEvent(entity_type="card", entity_id=card.id, expires_at=utcnow() + timedelta(days=31)))


async def image_keys(session: AsyncSession, condition) -> list[str]:
    return list((await session.scalars(select(CardImage.storage_key).join(Card).where(condition))).all())


async def purge_deleted(session: AsyncSession, report: MaintenanceReport) -> list[str]:
    users = list((await session.scalars(select(User.id).where(User.deletion_requested_at.is_not(None)))).all())
    cards = list((await session.scalars(select(Card.id).where(Card.deleted_at.is_not(None), Card.user_id.not_in(users)))).all())
    keys = await image_keys(session, or_(Card.user_id.in_(users), Card.id.in_(cards)))
    report.purged_users, report.purged_cards, report.removed_files = len(users), len(cards), len(keys)
    if report.dry_run:
        return []
    await session.execute(delete(Card).where(Card.id.in_(cards)))
    await session.execute(delete(User).where(User.id.in_(users)))
    await session.execute(update(DeletionEvent).where(
        DeletionEvent.entity_id.in_([*users, *cards]), DeletionEvent.purged_at.is_(None),
    ).values(purged_at=utcnow()))
    return keys


async def trim_jobs(session: AsyncSession, report: MaintenanceReport) -> None:
    old_payload = and_(GenerationJob.status.in_(TERMINAL), GenerationJob.finished_at < utcnow() - timedelta(days=7),
                       or_(GenerationJob.input_snapshot_json.is_not(None), GenerationJob.result_json.is_not(None)))
    old_jobs = and_(GenerationJob.status.in_(TERMINAL), GenerationJob.finished_at < utcnow() - timedelta(days=90))
    report.cleared_payloads = await count(session, select(GenerationJob.id).where(old_payload))
    report.deleted_jobs = await count(session, select(GenerationJob.id).where(old_jobs))
    expired = and_(DeletionEvent.purged_at.is_not(None), DeletionEvent.expires_at < utcnow())
    report.expired_registry = await count(session, select(DeletionEvent.id).where(expired))
    if not report.dry_run:
        await session.execute(update(GenerationJob).where(old_payload).values(input_snapshot_json=None, result_json=None))
        await session.execute(delete(GenerationJob).where(old_jobs))
        await session.execute(delete(DeletionEvent).where(expired))


def remove_files(root: Path, keys: list[str]) -> None:
    for key in keys:
        if STORAGE_KEY.fullmatch(key):
            try:
                (root / key).unlink(missing_ok=True)
            except OSError:
                logger.error("file_remove_failed")


def orphan_candidates(root: Path, known: set[str]) -> list[str]:
    if not root.is_dir():
        return []
    cutoff = (utcnow() - timedelta(hours=24)).timestamp()
    orphans = []
    for path in root.iterdir():
        try:
            if STORAGE_KEY.fullmatch(path.name) and path.name not in known and path.stat().st_mtime < cutoff:
                orphans.append(path.name)
        except OSError:
            continue
    return orphans


async def run_maintenance(db: Database, settings: Settings, dry_run: bool = False) -> MaintenanceReport:
    report = MaintenanceReport(dry_run=dry_run)
    async with db.transaction() as session:
        await advisory_lock(session, "maintenance")
        await expire_credentials(session, report)
        await expire_drafts(session, report)
    async with db.transaction() as session:
        await advisory_lock(session, "maintenance")
        await advisory_lock(session, "generation-queue")
        keys = await purge_deleted(session, report)
        await trim_jobs(session, report)
    await asyncio.to_thread(remove_files, settings.upload_dir, keys)
    async with db.transaction() as session:
        await advisory_lock(session, "maintenance")
        known = set((await session.scalars(select(CardImage.storage_key))).all())
        orphans = await asyncio.to_thread(orphan_candidates, settings.upload_dir, known)
    report.orphan_files = len(orphans)
    if not dry_run:
        await asyncio.to_thread(remove_files, settings.upload_dir, orphans)
    audit(settings, "maintenance", asdict(report))
    return report


async def quota_problems(db: Database) -> list[str]:
    problems: list[str] = []
    async with db.transaction() as session:
        reserved = {(row.user_id, row.quota_period, row.quota_bucket): row.total for row in (await session.execute(
            select(GenerationJob.user_id, GenerationJob.quota_period, GenerationJob.quota_bucket,
                   func.count().label("total")).where(GenerationJob.quota_state == "reserved")
            .group_by(GenerationJob.user_id, GenerationJob.quota_period, GenerationJob.quota_bucket))).all()}
        for usage in (await session.scalars(select(MonthlyUsage))).all():
            for bucket in ("initial", "regeneration"):
                expected = reserved.pop((usage.user_id, usage.period, bucket), 0)
                if getattr(usage, f"{bucket}_reserved") != expected:
                    problems.append(f"quota_reserved_mismatch user={usage.user_id} period={usage.period} bucket={bucket}")
        problems += [f"quota_row_missing user={user} period={period} bucket={bucket}" for user, period, bucket in reserved]
        inconsistent = await count(session, select(GenerationJob.id).where(or_(
            and_(GenerationJob.status.in_(TERMINAL), GenerationJob.quota_state == "reserved"),
            and_(GenerationJob.status.in_(("queued", "running")), GenerationJob.quota_state != "reserved"))))
        if inconsistent:
            problems.append(f"job_quota_state_mismatch count={inconsistent}")
        problems += await budget_problems(session)
    return problems


async def budget_problems(session: AsyncSession) -> list[str]:
    reserved = dict((await session.execute(select(AICallUsage.budget_day, func.sum(AICallUsage.reserved_cost))
                                           .where(AICallUsage.budget_state == "reserved").group_by(AICallUsage.budget_day))).all())
    problems = []
    for budget in (await session.scalars(select(DailyBudget))).all():
        if budget.reserved_usd != (reserved.pop(budget.day, None) or 0):
            problems.append(f"budget_reserved_mismatch day={budget.day}")
    return problems + [f"budget_row_missing day={day}" for day in reserved]


async def apply_deletions(db: Database, events: list[dict[str, Any]]) -> int:
    applied = 0
    async with db.transaction() as session:
        await advisory_lock(session, "maintenance")
        for event in events:
            entity_id = UUID(event["entity_id"])
            model, column = (User, User.deletion_requested_at) if event["entity_type"] == "user" else (Card, Card.deleted_at)
            result = await session.execute(update(model).where(model.id == entity_id, column.is_(None)).values({column: utcnow()}))
            applied += result.rowcount or 0
            exists = await session.scalar(select(DeletionEvent.id).where(DeletionEvent.entity_id == entity_id))
            if not exists:
                session.add(DeletionEvent(entity_type=event["entity_type"], entity_id=entity_id,
                                          expires_at=utcnow() + timedelta(days=31)))
    return applied
