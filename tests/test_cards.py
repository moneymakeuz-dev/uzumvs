import asyncio
import io
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.errors import AppError
from app.models import Card, DailyUsage, MonthlyUsage
from app.schemas import CardPatch
from app.services.cards import create_card, owned_card, save_card
from app.services.images import prepare_image
from app.services.quotas import reserve

pytestmark = pytest.mark.integration


def prepared_image():
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(stream, "PNG")
    return prepare_image(stream.getvalue())


async def test_idempotent_draft_ownership_and_versions(database, account):
    db, settings = database
    images = [prepared_image()]
    first = await create_card(db, settings, account.id, "Oq chashka", settings.consent_version, "first", images)
    duplicate = await create_card(db, settings, account.id, "Oq chashka", settings.consent_version, "first", images)
    assert first["id"] == duplicate["id"]
    from uuid import UUID
    async with db.transaction() as session:
        with pytest.raises(AppError) as error:
            await owned_card(session, uuid4(), UUID(first["id"]))
        assert error.value.status == 404
        result = await save_card(session, account.id, UUID(first["id"]), CardPatch(expected_version=1, seller_notes="Yangi izoh"))
        assert result["version"] == 2
        with pytest.raises(AppError):
            await save_card(session, account.id, UUID(first["id"]), CardPatch(expected_version=1, seller_notes="Eski izoh"))
        assert (await session.scalar(select(DailyUsage))).created_drafts == 1
    assert len(list(settings.upload_dir.glob("*.jpg"))) == 1
    with pytest.raises(AppError):
        await create_card(db, settings, account.id, "Boshqa izoh", settings.consent_version, "first", images)


async def test_last_quota_unit_is_atomic(database, account):
    db, settings = database
    settings.initial_monthly_limit = 1

    async def attempt():
        try:
            async with db.transaction() as session:
                await reserve(session, account.id, "initial", settings)
            return "reserved"
        except AppError:
            return "rejected"

    assert sorted(await asyncio.gather(attempt(), attempt())) == ["rejected", "reserved"]
    async with db.transaction() as session:
        monthly = await session.scalar(select(MonthlyUsage))
        assert monthly.initial_reserved == 1
        assert (await session.scalar(select(DailyUsage))).accepted_jobs == 1


async def test_failed_upload_has_no_card(database, account):
    db, settings = database
    with pytest.raises(AppError):
        await create_card(db, settings, account.id, "", "wrong", "empty", [])
    async with db.transaction() as session:
        assert await session.scalar(select(Card)) is None