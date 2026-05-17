from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task


@pytest.mark.asyncio
async def test_engine_can_connect():
    """Verify in-memory SQLite async engine accepts a connection and runs SELECT 1."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        row = result.scalar()
    await engine.dispose()
    assert row == 1


def test_models_are_registered():
    """Verify all three ORM models are registered on Base.metadata."""
    table_names = set(Base.metadata.tables.keys())
    assert "task" in table_names
    assert "task_item" in table_names
    assert "task_event" in table_names


def test_alembic_upgrade_creates_tables(tmp_path):
    """Run `alembic upgrade head` programmatically and verify tables are created."""
    from alembic.config import Config
    from sqlalchemy import create_engine

    from alembic import command

    db_path = tmp_path / "test_migration.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    db_url_sync = f"sqlite:///{db_path}"

    # Point settings at the temp DB before alembic reads it.
    os.environ["DATABASE_URL"] = db_url_async
    try:
        from app.core.settings import get_settings

        get_settings.cache_clear()

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "alembic")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url_async)

        command.upgrade(alembic_cfg, "head")
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()

    # Inspect with sync engine to check tables.
    sync_engine = create_engine(db_url_sync)
    with sync_engine.connect():
        insp = inspect(sync_engine)
        existing = set(insp.get_table_names())
    sync_engine.dispose()

    assert "task" in existing
    assert "task_item" in existing
    assert "task_event" in existing


@pytest.mark.asyncio
async def test_create_task_via_orm():
    """Create a Task via async ORM session, commit, re-query, and verify fields."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    idempotency_key = "test-idem-key-001"

    async with session_maker() as session:
        task = Task(
            idempotency_key=idempotency_key,
            submission_label="test_upload.zip",
            temp_dir="/tmp/test_task",
            summary_json={"total": 0, "ok": 0, "warning": 0, "error": 0, "ignored": 0,
                          "has_blocking_errors": False},
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async with session_maker() as session:
        fetched = await session.get(Task, task_id)

    await engine.dispose()

    assert fetched is not None
    assert fetched.id == task_id
    assert len(fetched.id) == 12
    assert fetched.idempotency_key == idempotency_key
    assert fetched.submission_label == "test_upload.zip"
    assert fetched.status == "draft"
    assert fetched.created_by == "local-user"
