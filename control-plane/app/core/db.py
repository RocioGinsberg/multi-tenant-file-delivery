from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings


def gen_id() -> str:
    """Generate a 12-character hex string primary key."""
    return secrets.token_hex(6)


def _make_engine() -> AsyncEngine:
    """Create async engine from configured database URL."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
    )


async_engine: AsyncEngine = _make_engine()

async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession for FastAPI dependency injection."""
    async with async_session_maker() as session:
        yield session
