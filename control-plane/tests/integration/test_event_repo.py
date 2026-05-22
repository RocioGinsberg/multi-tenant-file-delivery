from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task, TaskEvent
from app.repos.event_repo import EventRepo


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        yield db_session

    await engine.dispose()


async def _create_task(
    session: AsyncSession,
    idempotency_key: str = "idem-event",
    *,
    owner_tenant_id: str = "hq",
    owner_user_id: str = "local-user",
) -> Task:
    task = Task(
        idempotency_key=idempotency_key,
        owner_tenant_id=owner_tenant_id,
        owner_user_id=owner_user_id,
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_event_repo_append_and_list_by_task_orders_events(session: AsyncSession):
    repo = EventRepo()
    task = await _create_task(session)

    first = await repo.append(session, task.id, "task_created")
    second = await repo.append(session, task.id, "classified", {"ok": 2})
    first.created_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    second.created_at = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
    await session.flush()

    events = await repo.list_by_task(session, task.id)
    missing_events = await repo.list_by_task(session, "missing")

    assert [event.id for event in events] == [first.id, second.id]
    assert events[0].payload_json == {}
    assert events[1].payload_json == {"ok": 2}
    assert missing_events == []


@pytest.mark.asyncio
async def test_event_repo_list_by_task_honors_tenant_filter(session: AsyncSession):
    repo = EventRepo()
    hq_task = await _create_task(session, "idem-event-hq", owner_tenant_id="hq")
    sub_task = await _create_task(
        session,
        "idem-event-sub",
        owner_tenant_id="subsidiary-a",
        owner_user_id="sub-user",
    )
    hq_event = await repo.append(session, hq_task.id, "task_created")
    sub_event = await repo.append(session, sub_task.id, "task_created")

    visible_events = await repo.list_by_task(session, hq_task.id, tenant_id="hq")
    hidden_events = await repo.list_by_task(session, hq_task.id, tenant_id="subsidiary-a")
    sub_events = await repo.list_by_task(session, sub_task.id, tenant_id="subsidiary-a")

    assert [event.id for event in visible_events] == [hq_event.id]
    assert hidden_events == []
    assert [event.id for event in sub_events] == [sub_event.id]


@pytest.mark.asyncio
async def test_event_repo_does_not_commit_transaction_boundary():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    repo = EventRepo()

    async with session_maker() as session:
        task = Task(idempotency_key="idem-event-rollback")
        session.add(task)
        await session.flush()
        event = await repo.append(session, task.id, "task_created")
        event_id = event.id
        await session.rollback()

    async with session_maker() as session:
        assert await session.get(TaskEvent, event_id) is None

    await engine.dispose()
