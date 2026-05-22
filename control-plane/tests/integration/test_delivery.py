from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from opentelemetry import context, trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.event_repo import EventRepo
from app.repos.item_repo import ItemRepo
from app.services.delivery import (
    DeliveryResultMessage,
    DeliveryResultRecord,
    DeliverySourceReference,
    FileSpoolDeliveryPublisher,
    FileSpoolDeliveryResultConsumer,
    KafkaDeliveryPublisher,
    KafkaDeliveryResultConsumer,
    apply_delivery_result,
    build_delivery_task_message,
    consume_delivery_results,
)
from app.services.redis_lease import LeaseClaim

TRACEPARENT = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"


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
async def test_build_delivery_task_message_injects_current_traceparent():
    task = SimpleNamespace(
        id="task-trace",
        idempotency_key="idem-trace",
        submission_label="upload.zip",
        temp_dir="/tmp/task-trace",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    span_context = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    token = context.attach(trace.set_span_in_context(NonRecordingSpan(span_context)))
    try:
        with patch("app.services.tracing.tracing_enabled", return_value=True):
            message = build_delivery_task_message(
                task=task,
                upload_items=[],
                bucket_name="auto-upload-dev",
            )
    finally:
        context.detach(token)

    assert message.traceparent == TRACEPARENT


@pytest.mark.asyncio
async def test_build_delivery_task_message_keeps_traceparent_empty_when_disabled():
    task = SimpleNamespace(
        id="task-no-trace",
        idempotency_key="idem-no-trace",
        submission_label="upload.zip",
        temp_dir="/tmp/task-no-trace",
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

    assert message.traceparent is None


@pytest.mark.asyncio
async def test_build_delivery_task_message_can_emit_source_reference_payload():
    task = SimpleNamespace(
        id="task-source",
        idempotency_key="idem-source",
        submission_label="upload.zip",
        temp_dir="/tmp/task-source",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    item = SimpleNamespace(
        id="item-source",
        src_path="acme/report.xlsx",
        filename="report.xlsx",
        ext=".xlsx",
        file_size=123,
        dst_path="reports/report.xlsx",
        severity="ok",
        target_name_raw="acme",
        target_name_matched="acme",
        document_type="report",
        category_name="reports",
        upload_status="pending",
    )

    message = build_delivery_task_message(
        task=task,
        upload_items=[item],
        bucket_name="auto-upload-dev",
        source=DeliverySourceReference(
            bucket="auto-upload-staging",
            key="staged/tasks/task-source/archive.zip",
            sha256="abc",
            size=456,
        ),
    )

    payload = message.to_payload()
    assert payload["schema_version"] == 2
    assert payload["source"]["bucket"] == "auto-upload-staging"
    assert payload["source"]["key"] == "staged/tasks/task-source/archive.zip"
    assert payload["items"][0]["src_path"] == "acme/report.xlsx"
    assert payload["items"][0]["source_path"] == "acme/report.xlsx"


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
async def test_kafka_delivery_publisher_sends_json_payload():
    class FakeProducer:
        def __init__(self):
            self.sent = []

        async def send_and_wait(self, topic, value, **kwargs):
            self.sent.append((topic, value, kwargs))

    task = SimpleNamespace(
        id="task-kafka",
        idempotency_key="idem-kafka",
        submission_label="upload.zip",
        temp_dir="/tmp/task-kafka",
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
    producer = FakeProducer()

    await KafkaDeliveryPublisher(
        bootstrap_servers="localhost:9092",
        producer=producer,
    ).publish(message)

    assert len(producer.sent) == 1
    topic, value, kwargs = producer.sent[0]
    payload = json.loads(value.decode("utf-8"))
    assert topic == "delivery.tasks.v1"
    assert kwargs["key"] == b"task-kafka"
    assert payload["task_id"] == "task-kafka"
    assert "headers" not in kwargs


@pytest.mark.asyncio
async def test_kafka_delivery_publisher_writes_traceparent_header():
    class FakeProducer:
        def __init__(self):
            self.sent = []

        async def send_and_wait(self, topic, value, **kwargs):
            self.sent.append((topic, value, kwargs))

    task = SimpleNamespace(
        id="task-kafka-trace",
        idempotency_key="idem-kafka-trace",
        submission_label="upload.zip",
        temp_dir="/tmp/task-kafka-trace",
        status="confirmed",
        created_by="local-user",
        created_at=None,
        confirmed_at=None,
    )
    message = build_delivery_task_message(
        task=task,
        upload_items=[],
        bucket_name="auto-upload-dev",
        traceparent=TRACEPARENT,
    )
    producer = FakeProducer()

    await KafkaDeliveryPublisher(
        bootstrap_servers="localhost:9092",
        producer=producer,
    ).publish(message)

    assert len(producer.sent) == 1
    _, value, kwargs = producer.sent[0]
    payload = json.loads(value.decode("utf-8"))
    assert payload["traceparent"] == TRACEPARENT
    assert kwargs["headers"] == [("traceparent", TRACEPARENT.encode("ascii"))]


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
async def test_apply_delivery_result_is_stable_for_duplicate_result(
    session: AsyncSession,
):
    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-duplicate-result", status="queued")
    session.add(task)
    await session.flush()
    item = (
        await item_repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "ok.xlsx", "filename": "ok.xlsx"}],
        )
    )[0]

    message = DeliveryResultMessage(
        task_id=task.id,
        status="uploaded",
        uploaded=1,
        failed=0,
        processed=1,
        started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
        ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        items=[{"item_id": item.id, "status": "uploaded"}],
    )

    first = await apply_delivery_result(session, message)
    second = await apply_delivery_result(session, message)

    updated_items = await item_repo.list_by_task(session, task.id)
    assert first.applied_items == 1
    assert second.applied_items == 1
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"
    assert updated_items[0].uploaded_at == message.ended_at


@pytest.mark.asyncio
async def test_apply_delivery_result_does_not_update_items_from_another_task(
    session: AsyncSession,
):
    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-result-owner", status="queued")
    other_task = Task(idempotency_key="idem-result-other", status="queued")
    session.add_all([task, other_task])
    await session.flush()
    other_item = (
        await item_repo.bulk_insert(
            session,
            other_task.id,
            [{"src_path": "other.xlsx", "filename": "other.xlsx"}],
        )
    )[0]

    summary = await apply_delivery_result(
        session,
        DeliveryResultMessage(
            task_id=task.id,
            status="uploaded",
            uploaded=1,
            failed=0,
            processed=1,
            started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
            ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            items=[{"item_id": other_item.id, "status": "uploaded"}],
        ),
    )

    assert summary.applied_items == 0
    assert summary.missing_items == [other_item.id]
    assert other_item.upload_status == "pending"


@pytest.mark.asyncio
async def test_apply_delivery_result_returns_missing_items_for_missing_task(
    session: AsyncSession,
):
    summary = await apply_delivery_result(
        session,
        DeliveryResultMessage(
            task_id="missing-task",
            status="uploaded",
            uploaded=1,
            failed=0,
            processed=1,
            started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
            ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            items=[{"item_id": "item-1", "status": "uploaded"}],
        ),
    )

    assert summary.applied_items == 0
    assert summary.missing_items == ["item-1"]


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


@pytest.mark.asyncio
async def test_consume_delivery_results_applies_kafka_messages_and_commits(
    session: AsyncSession,
):
    class FakeKafkaMessage:
        def __init__(self, value: bytes, *, offset: int = 0):
            self.offset = offset
            self.value = value

    class FakeTopicPartition:
        pass

    class FakeConsumer:
        def __init__(self, value: bytes):
            self.value = value
            self.commits = []
            self.stopped = False

        async def getmany(self, *, timeout_ms, max_records):
            assert timeout_ms == 1000
            assert max_records == 100
            return {FakeTopicPartition(): [FakeKafkaMessage(self.value)]}

        async def commit(self, offsets=None):
            self.commits.append(offsets)

        async def stop(self):
            self.stopped = True

    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-kafka-consume", status="queued")
    session.add(task)
    await session.flush()
    item = (
        await item_repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "ok.xlsx", "filename": "ok.xlsx"}],
        )
    )[0]

    payload = {
        "topic": "delivery.results.v1",
        "task_id": task.id,
        "status": "uploaded",
        "uploaded": 1,
        "failed": 0,
        "processed": 1,
        "started_at": "2026-05-17T11:59:00Z",
        "ended_at": "2026-05-17T12:00:00Z",
        "items": [{"item_id": item.id, "status": "uploaded"}],
    }
    consumer = FakeConsumer(json.dumps(payload).encode("utf-8"))

    summaries = await consume_delivery_results(
        session,
        KafkaDeliveryResultConsumer(
            bootstrap_servers="localhost:9092",
            consumer=consumer,
        ),
    )

    assert len(summaries) == 1
    assert len(consumer.commits) == 1
    committed_offsets = list(consumer.commits[0].values())
    assert committed_offsets == [1]
    assert consumer.stopped is False
    assert task.status == "uploaded"


@pytest.mark.asyncio
async def test_consume_delivery_results_commits_one_kafka_offset_at_a_time(
    session: AsyncSession,
):
    class FakeKafkaMessage:
        def __init__(self, value: bytes, offset: int):
            self.value = value
            self.offset = offset

    class FakeTopicPartition:
        pass

    class FakeConsumer:
        def __init__(self, values: list[bytes]):
            self.values = values
            self.commits = []
            self.stopped = False

        async def getmany(self, *, timeout_ms, max_records):
            return {
                FakeTopicPartition(): [
                    FakeKafkaMessage(value, index)
                    for index, value in enumerate(self.values)
                ]
            }

        async def commit(self, offsets=None):
            self.commits.append(offsets)

        async def stop(self):
            self.stopped = True

    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-kafka-offsets", status="queued")
    session.add(task)
    await session.flush()
    items = await item_repo.bulk_insert(
        session,
        task.id,
        [
            {"src_path": "one.xlsx", "filename": "one.xlsx"},
            {"src_path": "two.xlsx", "filename": "two.xlsx"},
        ],
    )

    def payload(item_id: str) -> bytes:
        return json.dumps({
            "topic": "delivery.results.v1",
            "task_id": task.id,
            "status": "uploaded",
            "uploaded": 1,
            "failed": 0,
            "processed": 1,
            "started_at": "2026-05-17T11:59:00Z",
            "ended_at": "2026-05-17T12:00:00Z",
            "items": [{"item_id": item_id, "status": "uploaded"}],
        }).encode("utf-8")

    consumer = FakeConsumer([payload(items[0].id), payload(items[1].id)])

    summaries = await consume_delivery_results(
        session,
        KafkaDeliveryResultConsumer(
            bootstrap_servers="localhost:9092",
            consumer=consumer,
        ),
    )

    assert len(summaries) == 2
    committed_offsets = [
        list(commit.values())[0]
        for commit in consumer.commits
    ]
    assert committed_offsets == [1, 2]


@pytest.mark.asyncio
async def test_consume_delivery_results_skips_record_when_lease_is_held(
    session: AsyncSession,
    monkeypatch,
):
    class HeldLeaseClient:
        def __init__(self) -> None:
            self.released = []
            self.closed = False

        async def acquire(self, resource: str) -> LeaseClaim:
            return LeaseClaim(key=resource, token="held", acquired=False)

        async def release(self, claim: LeaseClaim) -> None:
            self.released.append(claim)

        async def aclose(self) -> None:
            self.closed = True

    class OneRecordConsumer:
        def __init__(self, message: DeliveryResultMessage) -> None:
            self.acks = 0
            self.closed = False
            self.message = message

        async def consume_records(self):
            async def ack() -> None:
                self.acks += 1

            return [DeliveryResultRecord(message=self.message, ack=ack)]

        async def close(self) -> None:
            self.closed = True

    lease_client = HeldLeaseClient()
    monkeypatch.setattr(
        "app.services.delivery.create_redis_lease",
        lambda: lease_client,
    )

    task = Task(idempotency_key="idem-lease-held", status="queued")
    session.add(task)
    await session.flush()
    message = DeliveryResultMessage(
        task_id=task.id,
        status="uploaded",
        uploaded=0,
        failed=0,
        processed=0,
        started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
        ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )
    consumer = OneRecordConsumer(message)

    summaries = await consume_delivery_results(session, consumer)

    assert summaries == []
    assert task.status == "queued"
    assert consumer.acks == 0
    assert consumer.closed is True
    assert lease_client.released == []
    assert lease_client.closed is True


@pytest.mark.asyncio
async def test_consume_delivery_results_releases_acquired_lease(
    session: AsyncSession,
    monkeypatch,
):
    class AcquiredLeaseClient:
        def __init__(self) -> None:
            self.released = []
            self.closed = False

        async def acquire(self, resource: str) -> LeaseClaim:
            return LeaseClaim(key=resource, token="owner", acquired=True)

        async def release(self, claim: LeaseClaim) -> None:
            self.released.append(claim)

        async def aclose(self) -> None:
            self.closed = True

    class OneRecordConsumer:
        def __init__(self, message: DeliveryResultMessage) -> None:
            self.acks = 0
            self.message = message

        async def consume_records(self):
            async def ack() -> None:
                self.acks += 1

            return [DeliveryResultRecord(message=self.message, ack=ack)]

    lease_client = AcquiredLeaseClient()
    monkeypatch.setattr(
        "app.services.delivery.create_redis_lease",
        lambda: lease_client,
    )

    item_repo = ItemRepo()
    task = Task(idempotency_key="idem-lease-release", status="queued")
    session.add(task)
    await session.flush()
    item = (
        await item_repo.bulk_insert(
            session,
            task.id,
            [{"src_path": "ok.xlsx", "filename": "ok.xlsx"}],
        )
    )[0]
    message = DeliveryResultMessage(
        task_id=task.id,
        status="uploaded",
        uploaded=1,
        failed=0,
        processed=1,
        items=[{"item_id": item.id, "status": "uploaded"}],
        started_at=datetime(2026, 5, 17, 11, 59, tzinfo=UTC),
        ended_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )
    consumer = OneRecordConsumer(message)

    summaries = await consume_delivery_results(session, consumer)

    assert len(summaries) == 1
    assert consumer.acks == 1
    assert len(lease_client.released) == 1
    assert lease_client.released[0].key == f"delivery_result_apply:{task.id}"
    assert lease_client.closed is True
