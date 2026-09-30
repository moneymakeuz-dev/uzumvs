import asyncio
import json
import logging
import signal
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.ai import provider
from app.ai.rules import RULES_VERSION, check_business_rules, prepare_reviews
from app.clock import utcnow
from app.config import Settings, get_settings
from app.db import Database, advisory_lock
from app.errors import AppError
from app.models import GenerationJob, WorkerHeartbeat
from app.runtime import run
from app.services import budget, jobs

logger = logging.getLogger(__name__)


async def heartbeat(db: Database, worker_id: str, job_id: UUID | None = None) -> None:
    async with db.transaction() as session:
        await session.execute(insert(WorkerHeartbeat).values(worker_id=worker_id, last_seen_at=utcnow())
                              .on_conflict_do_update(index_elements=["worker_id"], set_={"last_seen_at": utcnow()}))
        if job_id:
            await session.execute(update(GenerationJob).where(
                GenerationJob.id == job_id, GenerationJob.worker_id == worker_id, GenerationJob.status == "running",
            ).values(heartbeat_at=utcnow(), lease_expires_at=utcnow() + timedelta(seconds=180)))


async def keep_alive(db: Database, worker_id: str, job_id: UUID) -> None:
    while True:
        await heartbeat(db, worker_id, job_id)
        await asyncio.sleep(15)


async def call_provider(db: Database, settings: Settings, job: GenerationJob) -> provider.ProviderResult:
    async with db.transaction() as session:
        await advisory_lock(session, "generation-queue")
        current = await session.get(GenerationJob, job.id)
        if not current or current.status != "running" or current.worker_id != job.worker_id:
            raise provider.ProviderFailure("worker_interrupted", called=False)
        usage = await budget.reserve_call(session, current, settings)
        call_id = usage.id
    try:
        async with asyncio.timeout(45):
            result = await provider.generate(settings, job.input_snapshot_json)
    except provider.ProviderFailure as failure:
        async with db.transaction() as session:
            await budget.settle_call(session, call_id, settings, called=failure.called)
        raise
    except (TimeoutError, httpx.HTTPError, OSError):
        async with db.transaction() as session:
            await budget.settle_call(session, call_id, settings)
        raise provider.ProviderFailure("provider_timeout") from None
    async with db.transaction() as session:
        await budget.settle_call(session, call_id, settings, result=result)
    return result


async def generate_job(db: Database, settings: Settings, job: GenerationJob) -> None:
    if (job.prompt_version != provider.PROMPT_VERSION or job.rules_version != RULES_VERSION
            or job.provider_model != settings.ai_model
            or job.input_snapshot_json.get("provider") != settings.ai_provider):
        raise provider.ProviderFailure("configuration_changed", called=False)
    for attempt in range(2):
        try:
            result = await call_provider(db, settings, job)
            content = provider.parse_result(result, job.input_snapshot_json)
            check_business_rules(content, job.field_path)
            content = prepare_reviews(content, job.field_path)
            await jobs.complete(db, job.id, job.worker_id, content, json.loads(result.text))
            return
        except ValueError:
            if attempt == 1:
                raise provider.ProviderFailure("invalid_ai_response") from None
        except provider.ProviderFailure as failure:
            if not failure.retryable or attempt == 1:
                raise
            await asyncio.sleep(min(failure.retry_after, 15))


async def process_job(db: Database, settings: Settings, job: GenerationJob) -> None:
    timer = asyncio.create_task(keep_alive(db, job.worker_id, job.id))
    try:
        async with asyncio.timeout(120):
            await generate_job(db, settings, job)
    except provider.ProviderFailure as failure:
        await jobs.fail(db, settings, job.id, failure.code)
    except AppError as error:
        await jobs.fail(db, settings, job.id, error.code)
    except TimeoutError:
        await jobs.fail(db, settings, job.id, "provider_timeout")
    except Exception as error:
        logger.error("job_failed id=%s type=%s", job.id, type(error).__name__)
        await jobs.fail(db, settings, job.id, "generation_failed")
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


async def run_once(db: Database, settings: Settings, worker_id: str = "test-worker", recover: bool = True) -> bool:
    await heartbeat(db, worker_id)
    if recover:
        await jobs.recover(db, settings)
    job = await jobs.claim(db, worker_id)
    if not job:
        return False
    await process_job(db, settings, job)
    return True


async def serve(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    db, worker_id, stop = Database(settings), uuid4().hex, asyncio.Event()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(event, stop.set)
        except NotImplementedError:
            signal.signal(event, lambda *_: stop.set())
    logger.info("worker_started id=%s provider=%s", worker_id, settings.ai_provider)
    last_recovery = 0.0
    try:
        while not stop.is_set():
            processed = False
            try:
                if not settings.maintenance_mode:
                    loop_time = asyncio.get_running_loop().time()
                    recover = loop_time - last_recovery >= 30
                    last_recovery = loop_time if recover else last_recovery
                    processed = await run_once(db, settings, worker_id, recover)
            except Exception as error:
                logger.error("worker_loop_failed type=%s", type(error).__name__)
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                continue
    finally:
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(serve())
