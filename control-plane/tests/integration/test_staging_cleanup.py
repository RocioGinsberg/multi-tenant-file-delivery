from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.event_repo import EventRepo
from app.services import staging_cleanup
from app.services.staging_cleanup import cleanup_staging_sources


class FakeS3Client:
    def __init__(self) -> None:
        self.deleted: list[dict[str, str]] = []

    async def delete_object(self, **kwargs) -> None:
        self.deleted.append(kwargs)


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
async def test_cleanup_deletes_expired_staging_source_for_terminal_task(
    monkeypatch,
    session: AsyncSession,
):
    fake_client = FakeS3Client()

    @asynccontextmanager
    async def fake_s3_client():
        yield fake_client

    monkeypatch.setattr(staging_cleanup, "_s3_client", fake_s3_client)

    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    task = Task(idempotency_key="idem-cleanup", status="uploaded")
    session.add(task)
    await session.flush()
    event = await EventRepo().append(session, task.id, "task_staged_source", {
        "bucket": "auto-upload-staging",
        "key": "staged/tasks/task-1/archive.zip",
        "sha256": "abc",
        "size": 123,
    })
    event.created_at = now - timedelta(days=2)
    await session.flush()

    summary = await cleanup_staging_sources(
        session,
        retention=timedelta(days=1),
        now=now,
    )

    events = await EventRepo().list_by_task(session, task.id)
    assert summary.deleted == 1
    assert summary.failed == 0
    assert fake_client.deleted == [
        {
            "Bucket": "auto-upload-staging",
            "Key": "staged/tasks/task-1/archive.zip",
        }
    ]
    assert events[-1].event_type == "task_staged_source_deleted"
    assert events[-1].payload_json["source_event_id"] == event.id


@pytest.mark.asyncio
async def test_cleanup_skips_non_terminal_or_unexpired_sources(
    monkeypatch,
    session: AsyncSession,
):
    fake_client = FakeS3Client()

    @asynccontextmanager
    async def fake_s3_client():
        yield fake_client

    monkeypatch.setattr(staging_cleanup, "_s3_client", fake_s3_client)

    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    event_repo = EventRepo()
    queued_task = Task(idempotency_key="idem-queued", status="queued")
    fresh_task = Task(idempotency_key="idem-fresh", status="uploaded")
    session.add_all([queued_task, fresh_task])
    await session.flush()
    queued_event = await event_repo.append(session, queued_task.id, "task_staged_source", {
        "bucket": "auto-upload-staging",
        "key": "staged/tasks/queued/archive.zip",
    })
    fresh_event = await event_repo.append(session, fresh_task.id, "task_staged_source", {
        "bucket": "auto-upload-staging",
        "key": "staged/tasks/fresh/archive.zip",
    })
    queued_event.created_at = now - timedelta(days=2)
    fresh_event.created_at = now
    await session.flush()

    summary = await cleanup_staging_sources(
        session,
        retention=timedelta(days=1),
        now=now,
    )

    assert summary.scanned == 0
    assert summary.deleted == 0
    assert fake_client.deleted == []


@pytest.mark.asyncio
async def test_cleanup_skips_already_deleted_source(monkeypatch, session: AsyncSession):
    fake_client = FakeS3Client()

    @asynccontextmanager
    async def fake_s3_client():
        yield fake_client

    monkeypatch.setattr(staging_cleanup, "_s3_client", fake_s3_client)

    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    event_repo = EventRepo()
    task = Task(idempotency_key="idem-deleted", status="uploaded")
    session.add(task)
    await session.flush()
    source = {
        "bucket": "auto-upload-staging",
        "key": "staged/tasks/deleted/archive.zip",
    }
    source_event = await event_repo.append(session, task.id, "task_staged_source", source)
    source_event.created_at = now - timedelta(days=2)
    await event_repo.append(session, task.id, "task_staged_source_deleted", source)
    await session.flush()

    summary = await cleanup_staging_sources(
        session,
        retention=timedelta(days=1),
        now=now,
    )

    assert summary.scanned == 1
    assert summary.skipped == 1
    assert summary.deleted == 0
    assert fake_client.deleted == []
