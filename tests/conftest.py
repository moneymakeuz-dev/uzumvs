import asyncio
import os
import sys

import pytest
import pytest_asyncio
from dotenv import dotenv_values

from app.config import ROOT, Settings
from app.db import Base, Database
from app.models import User
from app.security import hash_password

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest_asyncio.fixture
async def database(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    if not database_url.split("?")[0].endswith("/karto_test"):
        raise RuntimeError("Integration tests may only reset the dedicated karto_test database")
    settings = Settings(_env_file=None, app_env="test", database_url=database_url,
                        ai_provider="mock", mail_backend="file", mail_dir=tmp_path / "mail",
                        upload_dir=tmp_path / "uploads", backup_dir=tmp_path / "backups",
                        audit_log=tmp_path / "audit.log")
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield database, settings
    await database.close()


@pytest_asyncio.fixture
async def account(database):
    from app.clock import utcnow

    db, _ = database
    async with db.transaction() as session:
        user = User(email_normalized="seller@example.com", password_hash=hash_password("local-test-password-42"),
                    email_verified_at=utcnow(), ai_consent_at=utcnow(), consent_version="2026-09-28")
        session.add(user)
        await session.flush()
    return user
