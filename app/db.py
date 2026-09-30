from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True, pool_size=5,
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    async def ready(self) -> bool:
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            return revision == "0001"

    async def close(self) -> None:
        await self.engine.dispose()


async def advisory_lock(session: AsyncSession, name: str) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), {"name": name})
