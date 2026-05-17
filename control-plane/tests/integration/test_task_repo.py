from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.task_repo import TaskRepo


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        yield db_session

    await engine.dispose()


@pytest.mark.asyncio
async def test_task_repo_create_and_get_returns_persisted_task(session: AsyncSession):
    repo = TaskRepo()

    task = await repo.create(
        session,
        idempotency_key="idem-001",
        submission_label="upload.zip",
        temp_dir="/tmp/task-001",
        summary_json={"total": 3},
    )

    assert task.id
    assert len(task.id) == 12
    assert task.status == "draft"
    assert task.created_by == "local-user"

    fetched = await repo.get(session, task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.idempotency_key == "idem-001"
    assert fetched.submission_label == "upload.zip"
    assert fetched.temp_dir == "/tmp/task-001"
    assert fetched.summary_json == {"total": 3}


@pytest.mark.asyncio
async def test_task_repo_get_by_idempotency_key_returns_existing_task(session: AsyncSession):
    repo = TaskRepo()
    task = await repo.create(session, idempotency_key="idem-lookup")

    fetched = await repo.get_by_idempotency_key(session, "idem-lookup")
    missing = await repo.get_by_idempotency_key(session, "missing")

    assert fetched is not None
    assert fetched.id == task.id
    assert missing is None


@pytest.mark.asyncio
async def test_task_repo_update_status_updates_only_explicit_timestamp_fields(
    session: AsyncSession,
):
    repo = TaskRepo()
    task = await repo.create(session, idempotency_key="idem-status")

    confirmed_at = datetime(2026, 5, 10, 8, 30, tzinfo=UTC)
    updated = await repo.update_status(
        session,
        task.id,
        "confirmed",
        confirmed_at=confirmed_at,
    )
    missing = await repo.update_status(session, "missing", "failed")

    assert updated is not None
    assert updated.status == "confirmed"
    assert updated.confirmed_at == confirmed_at
    assert updated.finished_at is None
    assert missing is None


@pytest.mark.asyncio
async def test_task_repo_list_orders_newest_first_and_applies_pagination(
    session: AsyncSession,
):
    repo = TaskRepo()
    older = await repo.create(session, idempotency_key="idem-older")
    newer = await repo.create(session, idempotency_key="idem-newer")
    newest = await repo.create(session, idempotency_key="idem-newest")

    older.created_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    newer.created_at = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
    newest.created_at = datetime(2026, 5, 10, 10, 0, tzinfo=UTC)
    await session.flush()

    first_page = await repo.list(session, limit=2)
    second_page = await repo.list(session, limit=2, offset=2)

    assert [task.id for task in first_page] == [newest.id, newer.id]
    assert [task.id for task in second_page] == [older.id]


@pytest.mark.asyncio
async def test_task_repo_does_not_commit_transaction_boundary():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    repo = TaskRepo()

    async with session_maker() as session:
        task = await repo.create(session, idempotency_key="idem-rollback")
        task_id = task.id
        await session.rollback()

    async with session_maker() as session:
        assert await session.get(Task, task_id) is None

    await engine.dispose()
