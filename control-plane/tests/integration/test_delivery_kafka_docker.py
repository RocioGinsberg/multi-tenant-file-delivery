from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.item_repo import ItemRepo
from app.services.delivery import (
    KafkaDeliveryPublisher,
    KafkaDeliveryResultConsumer,
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


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_control_plane_kafka_delivery_round_trip(session: AsyncSession):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml kafka running")

    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    task_topic = f"delivery.tasks.pytest.{suffix}"
    result_topic = f"delivery.results.pytest.{suffix}"

    task = SimpleNamespace(
        id="task-kafka-docker",
        idempotency_key="idem-kafka-docker",
        submission_label="upload.zip",
        temp_dir="/tmp/task-kafka-docker",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    task_message = build_delivery_task_message(
        task=task,
        upload_items=[],
        bucket_name="auto-upload-dev",
    )

    await KafkaDeliveryPublisher(
        bootstrap_servers=brokers,
        topic=task_topic,
    ).publish(task_message)

    task_consumer = AIOKafkaConsumer(
        task_topic,
        bootstrap_servers=brokers,
        group_id=f"control-plane-task-test-{suffix}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await task_consumer.start()
    try:
        consumed_task = await asyncio.wait_for(task_consumer.getone(), timeout=10)
    finally:
        await task_consumer.stop()

    payload = json.loads(consumed_task.value.decode("utf-8"))
    assert payload["task_id"] == "task-kafka-docker"

    item_repo = ItemRepo()
    db_task = Task(idempotency_key="idem-kafka-result", status="queued")
    session.add(db_task)
    await session.flush()
    item = (
        await item_repo.bulk_insert(
            session,
            db_task.id,
            [{"src_path": "ok.xlsx", "filename": "ok.xlsx"}],
        )
    )[0]

    result_payload = {
        "topic": "delivery.results.v1",
        "task_id": db_task.id,
        "status": "uploaded",
        "uploaded": 1,
        "failed": 0,
        "processed": 1,
        "started_at": "2026-05-17T11:59:00Z",
        "ended_at": "2026-05-17T12:00:00Z",
        "items": [{"item_id": item.id, "status": "uploaded"}],
    }
    producer = AIOKafkaProducer(bootstrap_servers=brokers)
    await producer.start()
    try:
        await producer.send_and_wait(
            result_topic,
            json.dumps(result_payload).encode("utf-8"),
            key=db_task.id.encode("utf-8"),
        )
    finally:
        await producer.stop()

    summaries = await consume_delivery_results(
        session,
        KafkaDeliveryResultConsumer(
            bootstrap_servers=brokers,
            topic=result_topic,
            group_id=f"control-plane-result-test-{suffix}",
            timeout_ms=10_000,
        ),
    )
    updated_items = await item_repo.list_by_task(session, db_task.id)

    assert len(summaries) == 1
    assert summaries[0].applied_items == 1
    assert db_task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"
