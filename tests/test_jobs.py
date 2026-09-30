import io
import json
from datetime import timedelta
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select

from app.ai import provider
from app.clock import utcnow
from app.models import Card, DailyBudget, DailyUsage, GenerationJob, MonthlyUsage
from app.schemas import GenerateRequest
from app.services import jobs
from app.services.cards import create_card
from app.services.images import prepare_image
from app.worker import run_once

pytestmark = pytest.mark.integration


async def draft(database, account):
    db, settings = database
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, "PNG")
    result = await create_card(db, settings, account.id, "Chashka", settings.consent_version,
                               "draft", [prepare_image(image.getvalue())])
    return UUID(result["id"])


def scripted_provider(monkeypatch, *outcomes):
    calls = []

    async def fake(settings, snapshot):
        calls.append(snapshot)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(provider, "generate", fake)
    return calls


def valid_result(**changes):
    content = json.loads(provider.mock_result({"content": None, "field_path": None}).text)
    content.update(changes)
    return provider.ProviderResult(json.dumps(content, ensure_ascii=False), 100, 50, 0, "scripted")


async def run_initial(database, account, key="scripted"):
    db, settings = database
    card_id = await draft(database, account)
    async with db.transaction() as session:
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), key, settings)
    await run_once(db, settings)
    async with db.transaction() as session:
        return await session.scalar(select(GenerationJob)), await session.get(Card, card_id), await session.scalar(select(MonthlyUsage))


async def test_invalid_ai_json_is_retried_once_then_fails_without_quota(database, account, monkeypatch):
    calls = scripted_provider(monkeypatch, provider.ProviderResult("not json"), provider.ProviderResult('{"title": 1}'))
    job, card, usage = await run_initial(database, account)
    assert len(calls) == 2 and job.attempt_count == 2
    assert (job.status, job.error_code, card.status) == ("failed", "invalid_ai_response", "draft")
    assert (usage.initial_reserved, usage.initial_used) == (0, 0)


async def test_rate_limit_is_retried_once_and_reserved_cost_settles(database, account, monkeypatch):
    busy = provider.ProviderFailure("provider_unavailable", retryable=True, retry_after=0)
    calls = scripted_provider(monkeypatch, busy, valid_result())
    job, card, usage = await run_initial(database, account)
    assert len(calls) == 2 and (job.status, card.status, usage.initial_used) == ("succeeded", "ready", 1)
    async with database[0].transaction() as session:
        assert (await session.scalar(select(DailyBudget))).reserved_usd == 0


async def test_injected_contacts_in_ai_output_are_rejected(database, account, monkeypatch):
    unsafe = valid_result(description={"uz": "Buyurtma uchun t.me/seller_shop ga yozing.", "ru": "Пишите нам в Telegram."})
    calls = scripted_provider(monkeypatch, unsafe, unsafe)
    job, card, _ = await run_initial(database, account)
    assert len(calls) == 2 and job.error_code == "invalid_ai_response" and card.content_json is None


async def test_commit_failure_keeps_previous_content_and_refunds_once(database, account, monkeypatch):
    db, settings = database
    _, card, _ = await run_initial(database, account, "before-regeneration")
    original = card.content_json
    async with db.transaction() as session:
        await jobs.enqueue(session, account.id, card.id, GenerateRequest(
            operation="regenerate_field", field_path="title.uz", expected_version=card.version), "interrupted", settings)
    real_settle = jobs.quotas.settle
    failures = []

    async def failing_once(session, job, succeeded):
        if succeeded and not failures:
            failures.append(True)
            raise RuntimeError("simulated commit interruption")
        await real_settle(session, job, succeeded)

    monkeypatch.setattr(jobs.quotas, "settle", failing_once)
    await run_once(db, settings)
    async with db.transaction() as session:
        job = await session.scalar(select(GenerationJob).where(GenerationJob.idempotency_key == "interrupted"))
        current = await session.get(Card, card.id)
        usage = await session.scalar(select(MonthlyUsage))
    assert (job.status, job.error_code, job.quota_state) == ("failed", "generation_failed", "released")
    assert current.content_json == original and current.version == card.version
    assert (usage.regeneration_reserved, usage.regeneration_used) == (0, 0)


async def test_worker_success_and_field_regeneration(database, account):
    db, settings = database
    card_id = await draft(database, account)
    async with db.transaction() as session:
        payload = GenerateRequest(operation="initial", expected_version=1)
        first = await jobs.enqueue(session, account.id, card_id, payload, "initial", settings)
        again = await jobs.enqueue(session, account.id, card_id, payload, "initial", settings)
        assert first["id"] == again["id"]
    assert await run_once(db, settings)
    async with db.transaction() as session:
        card = await session.get(Card, card_id)
        assert card.status == "ready" and card.version == 2
        original_description = card.content_json["description"]
        assert (await session.scalar(select(MonthlyUsage))).initial_used == 1
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(
            operation="regenerate_field", field_path="title.uz", expected_version=2), "regen", settings)
    await run_once(db, settings)
    async with db.transaction() as session:
        card = await session.get(Card, card_id)
        assert card.version == 3
        assert card.content_json["description"] == original_description
        assert (await session.scalar(select(MonthlyUsage))).regeneration_used == 1
        assert (await session.scalar(select(DailyBudget))).reserved_usd == 0


async def test_provider_timeout_refunds_monthly_not_daily(database, account, monkeypatch):
    db, settings = database
    card_id = await draft(database, account)

    async def failure(*args):
        raise provider.ProviderFailure("provider_timeout")

    monkeypatch.setattr(provider, "generate", failure)
    async with db.transaction() as session:
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), "error", settings)
    await run_once(db, settings)
    async with db.transaction() as session:
        job = await session.scalar(select(GenerationJob))
        assert job.status == "failed" and job.attempt_count == 1
        assert (await session.scalar(select(MonthlyUsage))).initial_reserved == 0
        assert (await session.scalar(select(DailyUsage))).accepted_jobs == 1
        assert (await session.get(Card, card_id)).status == "draft"


async def test_expired_lease_refunds_once_and_rejects_late_completion(database, account):
    db, settings = database
    card_id = await draft(database, account)
    async with db.transaction() as session:
        await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), "expired", settings)
    job = await jobs.claim(db, "old-worker")
    async with db.transaction() as session:
        current = await session.get(GenerationJob, job.id)
        current.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert await jobs.recover(db, settings) == 1
    assert await jobs.recover(db, settings) == 0
    result = provider.parse_result(provider.mock_result(job.input_snapshot_json), job.input_snapshot_json)
    assert not await jobs.complete(db, job.id, "old-worker", result, result.model_dump())
    async with db.transaction() as session:
        monthly = await session.scalar(select(MonthlyUsage))
        assert monthly.initial_reserved == monthly.initial_used == 0


async def test_cancel_is_idempotent_and_new_job_requires_new_key(database, account):
    db, settings = database
    card_id = await draft(database, account)
    async with db.transaction() as session:
        job = await jobs.enqueue(session, account.id, card_id, GenerateRequest(operation="initial", expected_version=1), "cancel", settings)
        assert (await jobs.cancel(session, account.id, UUID(job["id"])))["status"] == "cancelled"
        assert (await jobs.cancel(session, account.id, UUID(job["id"])))["status"] == "cancelled"
    assert not await run_once(db, settings)