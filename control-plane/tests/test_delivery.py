from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.event_repo import EventRepo
from app.repos.item_repo import ItemRepo
from app.services.delivery import (
    DeliveryResultMessage,
    FileSpoolDeliveryPublisher,
    FileSpoolDeliveryResultConsumer,
    apply_delivery_result,
    build_delivery_task_message,
    consume_delivery_results,
)


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
async def test_build_delivery_task_message_filters_uploadable_items():
    task = SimpleNamespace(
        id="task-1",
        idempotency_key="idem-1",
        submission_label="upload.zip",
        temp_dir="/tmp/task-1",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    items = [
        SimpleNamespace(
            id="item-1",
            src_path="acme/report.xlsx",
            filename="report.xlsx",
            ext=".xlsx",
            file_size=1,
            dst_path="reports/report.xlsx",
            severity="ok",
            target_name_raw="acme",
            target_name_matched="acme",
            document_type="report",
            category_name="reports",
            upload_status="pending",
        ),
        SimpleNamespace(
            id="item-2",
            src_path="acme/bad.txt",
            filename="bad.txt",
            ext=".txt",
            file_size=1,
            dst_path="reports/bad.txt",
            severity="error",
            target_name_raw="acme",
            target_name_matched="acme",
            document_type="report",
            category_name="reports",
            upload_status="pending",
        ),
    ]

    message = build_delivery_task_message(
        task=task,
        upload_items=[items[0]],
        bucket_name="auto-upload-dev",
    )

    payload = message.to_payload()
    assert payload["topic"] == "delivery.tasks.v1"
    assert payload["bucket_name"] == "auto-upload-dev"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["dst_path"] == "reports/report.xlsx"


@pytest.mark.asyncio
async def test_file_spool_delivery_publisher_writes_json(tmp_path):
    task = SimpleNamespace(
        id="task-2",
        idempotency_key="idem-2",
        submission_label="upload.zip",
        temp_dir="/tmp/task-2",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    message = build_delivery_task_message(
        task=task,
        upload_items=[],
        bucket_name="auto-upload-dev",
    )

    publisher = FileSpoolDeliveryPublisher(tmp_path)
    out = await publisher.publish(message)

    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("{")


@pytest.mark.asyncio
async def test_apply_delivery_result_updates_task_and_items(session: AsyncSession):
    item_repo = ItemRepo()
    event_repo = EventRepo()
    task = Task(idempotency_key="idem-result", status="queued")
    session.add(task)
    await session.flush()
    items = await item_repo.bulk_insert(
        session,
        task.id,
        [
            {"src_path": "ok.xlsx", "filename": "ok.xlsx"},
            {"src_path": "bad.xlsx", "filename": "bad.xlsx"},
        ],
    )

    ended_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    summary = await apply_delivery_result(
        session,
        DeliveryResultMessage(
            task_id=task.id,
            status="partial_failed",
            uploaded=1,
            failed=1,
            processed=2,
            started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
            ended_at=ended_at,
            items=[
                {
                    "item_id": items[0].id,
                    "status": "uploaded",
                    "key": "reports/ok.xlsx",
                    "size": 5,
                    "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                },
                {
                    "item_id": items[1].id,
                    "status": "failed",
                    "error": "missing source",
                },
            ],
        ),
    )

    updated_items = await item_repo.list_by_task(session, task.id)
    events = await event_repo.list_by_task(session, task.id)

    assert summary.applied_items == 2
    assert summary.missing_items == []
    assert task.status == "partial_failed"
    assert task.finished_at == ended_at
    assert updated_items[0].upload_status == "failed"
    assert updated_items[0].upload_error == "missing source"
    assert updated_items[1].upload_status == "uploaded"
    assert updated_items[1].uploaded_at == ended_at
    assert events[-1].event_type == "delivery_result_applied"
    assert events[-1].payload_json["uploaded"] == 1


@pytest.mark.asyncio
async def test_file_spool_delivery_result_consumer_reads_messages(tmp_path):
    result_dir = tmp_path / "delivery.results.v1"
    result_dir.mkdir()
    payload = {
        "topic": "delivery.results.v1",
        "task_id": "task-1",
        "status": "uploaded",
        "uploaded": 1,
        "failed": 0,
        "processed": 1,
        "started_at": "2026-05-17T11:59:00Z",
        "ended_at": "2026-05-17T12:00:00Z",
        "items": [
            {
                "item_id": "item-1",
                "status": "uploaded",
                "key": "reports/ok.xlsx",
                "size": 5,
                "sha256": "abc",
            },
        ],
    }
    (result_dir / "task-1.json").write_text(json.dumps(payload), encoding="utf-8")

    messages = await FileSpoolDeliveryResultConsumer(tmp_path).consume()

    assert len(messages) == 1
    assert messages[0].task_id == "task-1"
    assert messages[0].items[0].key == "reports/ok.xlsx"


@pytest.mark.asyncio
async def test_consume_delivery_results_applies_file_spool_messages(
    session: AsyncSession,
    tmp_path,
):
    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-consume", status="queued")
    session.add(task)
    await session.flush()
    item = (
        await item_repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "ok.xlsx", "filename": "ok.xlsx"}],
        )
    )[0]

    result_dir = tmp_path / "delivery.results.v1"
    result_dir.mkdir()
    payload = {
        "topic": "delivery.results.v1",
        "task_id": task.id,
        "status": "uploaded",
        "uploaded": 1,
        "failed": 0,
        "processed": 1,
        "started_at": "2026-05-17T11:59:00Z",
        "ended_at": "2026-05-17T12:00:00Z",
        "items": [
            {
                "item_id": item.id,
                "status": "uploaded",
                "key": "reports/ok.xlsx",
                "size": 5,
                "sha256": "abc",
            },
        ],
    }
    (result_dir / f"{task.id}.json").write_text(json.dumps(payload), encoding="utf-8")

    summaries = await consume_delivery_results(
        session,
        FileSpoolDeliveryResultConsumer(tmp_path),
    )
    updated_items = await item_repo.list_by_task(session, task.id)

    assert len(summaries) == 1
    assert summaries[0].status == "uploaded"
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"
