from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task, TaskItem
from app.repos.item_repo import ItemRepo


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        yield db_session

    await engine.dispose()


async def _create_task(session: AsyncSession, idempotency_key: str = "idem-item") -> Task:
    task = Task(idempotency_key=idempotency_key)
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_item_repo_bulk_insert_and_list_by_task_orders_by_src_path(
    session: AsyncSession,
):
    repo = ItemRepo()
    task = await _create_task(session)

    await repo.bulk_insert(
        session,
        task.id,
        [
            {"src_path": "b/report.xlsx", "filename": "report-b.xlsx"},
            {"src_path": "a/report.xlsx", "filename": "report-a.xlsx"},
        ],
    )

    items = await repo.list_by_task(session, task.id)

    assert [item.src_path for item in items] == ["a/report.xlsx", "b/report.xlsx"]
    assert all(item.task_id == task.id for item in items)
    assert all(item.upload_status == "pending" for item in items)


@pytest.mark.asyncio
async def test_item_repo_update_upload_status_returns_none_for_missing_item(
    session: AsyncSession,
):
    repo = ItemRepo()
    task = await _create_task(session)
    item = (
        await repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "a/report.xlsx", "filename": "report.xlsx"}],
        )
    )[0]

    uploaded_at = datetime(2026, 5, 10, 11, 0, tzinfo=UTC)
    updated = await repo.update_upload_status(
        session,
        item.id,
        "uploaded",
        uploaded_at=uploaded_at,
    )
    missing = await repo.update_upload_status(session, "missing", "failed")

    assert updated is not None
    assert updated.upload_status == "uploaded"
    assert updated.upload_error == ""
    assert updated.uploaded_at == uploaded_at
    assert missing is None


@pytest.mark.asyncio
async def test_item_repo_count_by_status_groups_task_items_only(session: AsyncSession):
    repo = ItemRepo()
    task = await _create_task(session, "idem-count-a")
    other_task = await _create_task(session, "idem-count-b")

    task_items = await repo.bulk_insert(
        session,
        task.id,
        [
            {"src_path": "a.xlsx", "filename": "a.xlsx"},
            {"src_path": "b.xlsx", "filename": "b.xlsx"},
            {"src_path": "c.xlsx", "filename": "c.xlsx"},
        ],
    )
    other_items = await repo.bulk_insert(
        session,
        other_task.id,
        [{"src_path": "other.xlsx", "filename": "other.xlsx"}],
    )

    await repo.update_upload_status(session, task_items[0].id, "uploaded")
    await repo.update_upload_status(session, task_items[1].id, "failed", upload_error="boom")
    await repo.update_upload_status(session, other_items[0].id, "failed", upload_error="ignored")

    counts = await repo.count_by_status(session, task.id)

    assert counts == {"failed": 1, "pending": 1, "uploaded": 1}


@pytest.mark.asyncio
async def test_item_repo_batch_reset_failed_only_resets_failed_items_for_task(
    session: AsyncSession,
):
    repo = ItemRepo()
    task = await _create_task(session, "idem-reset-a")
    other_task = await _create_task(session, "idem-reset-b")
    failed_at = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    items = await repo.bulk_insert(
        session,
        task.id,
        [
            {"src_path": "failed.xlsx", "filename": "failed.xlsx"},
            {"src_path": "uploaded.xlsx", "filename": "uploaded.xlsx"},
        ],
    )
    other_items = await repo.bulk_insert(
        session,
        other_task.id,
        [{"src_path": "other-failed.xlsx", "filename": "other-failed.xlsx"}],
    )
    await repo.update_upload_status(
        session,
        items[0].id,
        "failed",
        upload_error="retry me",
        uploaded_at=failed_at,
    )
    await repo.update_upload_status(session, items[1].id, "uploaded", uploaded_at=failed_at)
    await repo.update_upload_status(
        session,
        other_items[0].id,
        "failed",
        upload_error="other",
    )

    reset_count = await repo.batch_reset_failed(session, task.id)
    reset_items = await repo.list_by_task(session, task.id)
    other_reset_items = await repo.list_by_task(session, other_task.id)

    by_path = {item.src_path: item for item in reset_items}
    assert reset_count == 1
    assert by_path["failed.xlsx"].upload_status == "pending"
    assert by_path["failed.xlsx"].upload_error == ""
    assert by_path["failed.xlsx"].uploaded_at is None
    assert by_path["uploaded.xlsx"].upload_status == "uploaded"
    assert other_reset_items[0].upload_status == "failed"


@pytest.mark.asyncio
async def test_item_repo_does_not_commit_transaction_boundary():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    repo = ItemRepo()

    async with session_maker() as session:
        task = Task(idempotency_key="idem-item-rollback")
        session.add(task)
        await session.flush()
        inserted = await repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "rollback.xlsx", "filename": "rollback.xlsx"}],
        )
        item_id = inserted[0].id
        await session.rollback()

    async with session_maker() as session:
        assert await session.get(TaskItem, item_id) is None

    await engine.dispose()
