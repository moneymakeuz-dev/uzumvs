import asyncio
import io
import json
import os
import tarfile
import time
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from PIL import Image
from sqlalchemy import select, update
from typer.testing import CliRunner

from app.cli import cli
from app.clock import periods, utcnow
from app.config import Settings
from app.errors import AppError
from app.models import (
    Card,
    DailyBudget,
    DeletionEvent,
    GenerationJob,
    MonthlyUsage,
    User,
    WorkerHeartbeat,
)
from app.schemas import GenerateRequest
from app.services import backup, budget, jobs, maintenance, monitor
from app.services.cards import create_card, delete_card
from app.services.images import prepare_image
from tests.helpers import add_user


def image():
    stream = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(stream, "PNG")
    return prepare_image(stream.getvalue())


async def new_card(database, account, key):
    db, settings = database
    return UUID((await create_card(db, settings, account.id, "Izoh", settings.consent_version, key, [image()]))["id"])


@pytest.mark.integration
async def test_deleted_card_is_purged_only_outside_dry_run(database, account):
    db, settings = database
    card_id = await new_card(database, account, "purge")
    async with db.transaction() as session:
        await delete_card(session, account.id, card_id, 1)
    report = await maintenance.run_maintenance(db, settings, dry_run=True)
    assert report.purged_cards == 1 and len(list(settings.upload_dir.glob("*.jpg"))) == 1
    report = await maintenance.run_maintenance(db, settings)
    assert report.purged_cards == 1 and not list(settings.upload_dir.glob("*.jpg"))
    async with db.transaction() as session:
        assert await session.get(Card, card_id) is None
        assert (await session.scalar(select(DeletionEvent))).purged_at is not None
    assert "maintenance" in settings.audit_log.read_text(encoding="utf-8")


@pytest.mark.integration
async def test_old_drafts_orphans_and_deleted_accounts(database, account):
    db, settings = database
    card_id = await new_card(database, account, "old-draft")
    async with db.transaction() as session:
        await session.execute(update(Card).where(Card.id == card_id).values(updated_at=utcnow() - timedelta(days=8)))
    old_orphan, new_orphan = settings.upload_dir / ("a" * 32 + ".jpg"), settings.upload_dir / ("b" * 32 + ".jpg")
    old_orphan.write_bytes(b"x")
    new_orphan.write_bytes(b"x")
    stale = time.time() - 2 * 86400
    os.utime(old_orphan, (stale, stale))
    report = await maintenance.run_maintenance(db, settings)
    assert (report.expired_drafts, report.purged_cards, report.orphan_files) == (1, 1, 1)
    assert not old_orphan.exists() and new_orphan.exists()
    async with db.transaction() as session:
        await session.execute(update(User).where(User.id == account.id).values(deletion_requested_at=utcnow()))
    assert (await maintenance.run_maintenance(db, settings)).purged_users == 1
    async with db.transaction() as session:
        assert await session.get(User, account.id) is None


@pytest.mark.integration
async def test_registry_reapplies_deletions_and_invariants_detect_drift(database, account):
    db, settings = database
    card_id = await new_card(database, account, "registry")
    assert await maintenance.apply_deletions(db, [{"entity_type": "card", "entity_id": str(card_id)}]) == 1
    assert (await maintenance.run_maintenance(db, settings)).purged_cards == 1
    card_id = await new_card(database, account, "queued")
    async with db.transaction() as session:
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), "job", settings)
    assert await maintenance.quota_problems(db) == []
    async with db.transaction() as session:
        await session.execute(update(MonthlyUsage).values(initial_reserved=0))
    assert any("quota_reserved_mismatch" in problem for problem in await maintenance.quota_problems(db))


@pytest.mark.integration
async def test_monitor_alerts(database, account):
    db, settings = database
    assert "worker_heartbeat_stale" in await monitor.collect_alerts(db, settings)
    card_id = await new_card(database, account, "monitor")
    async with db.transaction() as session:
        session.add(WorkerHeartbeat(worker_id="worker", last_seen_at=utcnow()))
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), "monitor", settings)
        await session.execute(update(GenerationJob).values(created_at=utcnow() - timedelta(minutes=3)))
        await session.execute(update(DailyBudget).where(DailyBudget.day == periods()[1]).values(limit_usd=10, spent_usd=9))
    alerts = await monitor.collect_alerts(db, settings)
    assert "worker_heartbeat_stale" not in alerts
    assert {"queue_delayed", "budget_80"} <= set(alerts)
    assert await monitor.worker_alive(db)


def test_encrypted_backup_roundtrip_and_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CHUNK_SIZE", 1024)
    key = Fernet.generate_key().decode()
    source, encrypted = tmp_path / "plain.bin", tmp_path / "data.kbk"
    source.write_bytes(os.urandom(5000))
    backup.encrypt_file(key, source, encrypted)
    backup.decrypt_file(key, encrypted, tmp_path / "restored.bin")
    assert (tmp_path / "restored.bin").read_bytes() == source.read_bytes()
    with pytest.raises(backup.BackupError, match="wrong"):
        backup.decrypt_file(Fernet.generate_key().decode(), encrypted, tmp_path / "wrong.bin")
    data = encrypted.read_bytes()
    first = len(backup.MAGIC)
    length = int.from_bytes(data[first:first + 4], "big")
    (tmp_path / "truncated.kbk").write_bytes(data[:first + 4 + length])
    with pytest.raises(backup.BackupError, match="truncated"):
        backup.decrypt_file(key, tmp_path / "truncated.kbk", tmp_path / "partial.bin")


def test_restore_rejects_unexpected_archive_entries(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(backup.BackupError, match="Unexpected"):
        backup.safe_extract(archive, destination)
    assert not (tmp_path / "escape.txt").exists()


def test_backup_key_command_outputs_valid_key():
    result = CliRunner().invoke(cli, ["create-backup-key"])
    assert result.exit_code == 0
    Fernet(result.stdout.strip().encode())


@pytest.mark.integration
async def test_parallel_money_reservations_never_exceed_daily_budget(database, account):
    db, settings = database
    other = await add_user(database, "second@example.com")
    job_ids = []
    for owner, key in ((account, "money-a"), (other, "money-b")):
        card_id = await new_card(database, owner, key)
        async with db.transaction() as session:
            job = await jobs.enqueue(session, owner.id, card_id, GenerateRequest(operation="initial", expected_version=1), key, settings)
        job_ids.append(UUID(job["id"]))
    priced = settings.model_copy(update={"ai_provider": "gemini", "ai_daily_budget_usd": Decimal("0.02")})

    async def reserve(job_id):
        try:
            async with db.transaction() as session:
                await budget.reserve_call(session, await session.get(GenerationJob, job_id), priced)
            return "reserved"
        except AppError as error:
            return error.code

    assert sorted(await asyncio.gather(*(reserve(job_id) for job_id in job_ids))) == ["budget_exhausted", "reserved"]
    async with db.transaction() as session:
        row = await session.get(DailyBudget, periods()[1])
    assert row.reserved_usd == budget.maximum_call_cost(priced) <= Decimal("0.02")


async def test_alerts_are_delivered_to_webhook(monkeypatch):
    received = []
    transport = httpx.MockTransport(lambda request: received.append(json.loads(request.content)) or httpx.Response(204))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(monitor.httpx, "AsyncClient", lambda **options: real_client(transport=transport, **options))
    settings = Settings(_env_file=None, alert_webhook_url="https://alerts.example.invalid/hook")
    assert await monitor.send_alerts(settings, ["worker_heartbeat_stale", "disk_90"])
    assert received[0]["codes"] == ["worker_heartbeat_stale", "disk_90"]
    assert "Worker 60 soniyadan" in received[0]["text"]
