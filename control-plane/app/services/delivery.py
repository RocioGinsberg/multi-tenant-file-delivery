from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.event_repo import EventRepo
from app.repos.item_repo import ItemRepo
from app.repos.task_repo import TaskRepo
from app.services.metrics import record_delivery_event
from app.services.redis_lease import LeaseClaim, create_redis_lease
from app.services.tracing import current_traceparent, start_trace_span


class DeliveryItemSpec(BaseModel):
    """One uploadable task item after Phase 1 classification.

    ``src_path`` is the archive-relative path persisted in task_item. The
    worker combines it with ``DeliveryTaskMessage.temp_dir`` to open bytes.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    src_path: str
    filename: str
    ext: str
    file_size: int
    dst_path: str
    severity: str
    target_name_raw: str
    target_name_matched: str | None
    document_type: str
    category_name: str
    upload_status: str = "pending"
    source_path: str | None = None


class DeliverySourceReference(BaseModel):
    """Durable source reference for schema_version=2 delivery tasks."""

    model_config = ConfigDict(extra="forbid")

    type: str = "object"
    bucket: str
    key: str
    sha256: str | None = None
    size: int | None = None


class DeliveryTaskMessage(BaseModel):
    """Control-plane to data-plane delivery task contract.

    This is intentionally derived from persisted task/task_item rows, not from
    the legacy business script. Classification stays owned by the control plane.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    topic: str = "delivery.tasks.v1"
    task_id: str
    idempotency_key: str
    submission_label: str
    temp_dir: str
    bucket_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: list[DeliveryItemSpec] = Field(default_factory=list)
    source: DeliverySourceReference | None = None
    traceparent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DeliveryResultItemSpec(BaseModel):
    """One data-plane item result from ``delivery.results.v1``."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    status: str
    key: str | None = None
    size: int | None = None
    sha256: str | None = None
    error: str | None = None


class DeliveryResultMessage(BaseModel):
    """Data-plane to control-plane result contract."""

    model_config = ConfigDict(extra="forbid")

    topic: str = "delivery.results.v1"
    task_id: str
    status: str
    uploaded: int = 0
    failed: int = 0
    processed: int = 0
    items: list[DeliveryResultItemSpec] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True, slots=True)
class DeliveryResultApplySummary:
    task_id: str
    status: str
    uploaded: int
    failed: int
    processed: int
    applied_items: int
    missing_items: list[str]


@dataclass(frozen=True, slots=True)
class DeliveryResultRecord:
    message: DeliveryResultMessage
    ack: Any


def build_delivery_task_message(
    *,
    task,
    upload_items: list[Any],
    bucket_name: str,
    source: DeliverySourceReference | dict[str, Any] | None = None,
    traceparent: str | None = None,
) -> DeliveryTaskMessage:
    """Build the data-plane task message from already-classified upload items."""
    items = [
        DeliveryItemSpec(
            item_id=item.id,
            src_path=item.src_path,
            filename=item.filename,
            ext=item.ext,
            file_size=item.file_size,
            dst_path=item.dst_path,
            severity=item.severity,
            target_name_raw=item.target_name_raw,
            target_name_matched=item.target_name_matched or None,
            document_type=item.document_type,
            category_name=item.category_name,
            upload_status=item.upload_status,
            source_path=item.src_path if source is not None else None,
        )
        for item in upload_items
    ]

    metadata = {
        "status": task.status,
        "created_by": task.created_by,
    }
    if task.created_at:
        metadata["created_at"] = task.created_at.isoformat()
    if task.confirmed_at:
        metadata["confirmed_at"] = task.confirmed_at.isoformat()

    return DeliveryTaskMessage(
        schema_version=2 if source is not None else 1,
        task_id=task.id,
        idempotency_key=task.idempotency_key,
        submission_label=task.submission_label,
        temp_dir=task.temp_dir,
        bucket_name=bucket_name,
        items=items,
        source=source,
        traceparent=traceparent if traceparent is not None else current_traceparent(),
        metadata=metadata,
    )


async def apply_delivery_result(
    session: AsyncSession,
    result: DeliveryResultMessage | dict[str, Any],
    *,
    task_repo: TaskRepo | None = None,
    item_repo: ItemRepo | None = None,
    event_repo: EventRepo | None = None,
) -> DeliveryResultApplySummary:
    """Apply a data-plane result event to persisted task/item state.

    Transaction ownership stays with the caller. This makes the same function
    usable from local file-spool polling and, later, Kafka consumption.
    """
    start = time.perf_counter()
    metric_status = "error"
    try:
        with start_trace_span(
            "control_plane.delivery.result_apply",
            attributes={"delivery.operation": "result_apply", "delivery.transport": "consumer"},
        ) as span:
            message = (
                result
                if isinstance(result, DeliveryResultMessage)
                else DeliveryResultMessage.model_validate(result)
            )
            if span is not None:
                span.set_attribute("delivery.task_id", message.task_id)
                span.set_attribute("delivery.result.status", message.status)
                span.set_attribute("delivery.result.item_count", len(message.items))
            task_repo = task_repo or TaskRepo()
            item_repo = item_repo or ItemRepo()
            event_repo = event_repo or EventRepo()
            task = await task_repo.get(session, message.task_id)
            if task is None:
                missing_items = [item.item_id for item in message.items]
                if span is not None:
                    span.set_attribute("delivery.result.applied_items", 0)
                    span.set_attribute("delivery.result.missing_items", len(missing_items))
                metric_status = "missing_task"
                return DeliveryResultApplySummary(
                    task_id=message.task_id,
                    status=message.status,
                    uploaded=message.uploaded,
                    failed=message.failed,
                    processed=message.processed,
                    applied_items=0,
                    missing_items=missing_items,
                )

            applied_items = 0
            missing_items: list[str] = []
            for item in message.items:
                if item.status == "uploaded":
                    updated = await item_repo.update_upload_status(
                        session,
                        item.item_id,
                        "uploaded",
                        task_id=message.task_id,
                        tenant_id=task.owner_tenant_id,
                        uploaded_at=message.ended_at,
                    )
                elif item.status == "failed":
                    updated = await item_repo.update_upload_status(
                        session,
                        item.item_id,
                        "failed",
                        task_id=message.task_id,
                        tenant_id=task.owner_tenant_id,
                        upload_error=(item.error or message.error or "upload failed")[:1000],
                    )
                else:
                    missing_items.append(item.item_id)
                    continue

                if updated is None:
                    missing_items.append(item.item_id)
                else:
                    applied_items += 1

            await task_repo.update_status(
                session,
                message.task_id,
                message.status,
                tenant_id=task.owner_tenant_id,
                finished_at=message.ended_at,
            )
            await event_repo.append(
                session,
                message.task_id,
                "delivery_result_applied",
                {
                    "topic": message.topic,
                    "status": message.status,
                    "uploaded": message.uploaded,
                    "failed": message.failed,
                    "processed": message.processed,
                    "applied_items": applied_items,
                    "missing_items": missing_items,
                },
            )
            if span is not None:
                span.set_attribute("delivery.result.applied_items", applied_items)
                span.set_attribute("delivery.result.missing_items", len(missing_items))
            metric_status = message.status
            return DeliveryResultApplySummary(
                task_id=message.task_id,
                status=message.status,
                uploaded=message.uploaded,
                failed=message.failed,
                processed=message.processed,
                applied_items=applied_items,
                missing_items=missing_items,
            )
    finally:
        record_delivery_event(
            "result_apply",
            "consumer",
            metric_status,
            time.perf_counter() - start,
        )


@dataclass(slots=True)
class FileSpoolDeliveryPublisher:
    """Durable local stand-in for the Phase 2 Kafka topic bridge.

    The control plane writes one JSON file per task into topic-named folders.
    The Go worker can consume these files locally during development while the
    Kafka transport is being introduced.
    """

    base_dir: Path | str
    topic: str = "delivery.tasks.v1"
    result_topic: str = "delivery.results.v1"
    _topic_dir: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._topic_dir = Path(self.base_dir) / self.topic

    async def publish(self, message: DeliveryTaskMessage) -> Path:
        start = time.perf_counter()
        metric_status = "error"
        try:
            with start_trace_span(
                "control_plane.delivery.task_publish",
                attributes={
                    "delivery.operation": "task_publish",
                    "delivery.transport": "file",
                    "messaging.destination.name": self.topic,
                    "delivery.task_id": message.task_id,
                    "delivery.item_count": len(message.items),
                },
            ):
                self._topic_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = self._topic_dir / f"{message.task_id}.json.tmp"
                final_path = self._topic_dir / f"{message.task_id}.json"
                tmp_path.write_text(
                    message.model_dump_json(indent=2, exclude_none=True),
                    encoding="utf-8",
                )
                tmp_path.replace(final_path)
                metric_status = "published"
                return final_path
        finally:
            record_delivery_event(
                "task_publish",
                "file",
                metric_status,
                time.perf_counter() - start,
            )


@dataclass(slots=True)
class KafkaDeliveryPublisher:
    """Kafka producer for ``delivery.tasks.v1`` task events."""

    bootstrap_servers: str
    topic: str = "delivery.tasks.v1"
    producer: Any | None = None

    async def publish(self, message: DeliveryTaskMessage) -> None:
        start = time.perf_counter()
        metric_status = "error"
        try:
            with start_trace_span(
                "control_plane.delivery.task_publish",
                attributes={
                    "delivery.operation": "task_publish",
                    "delivery.transport": "kafka",
                    "messaging.system": "kafka",
                    "messaging.destination.name": self.topic,
                    "delivery.task_id": message.task_id,
                    "delivery.item_count": len(message.items),
                },
            ):
                producer = self.producer or AIOKafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                )
                should_manage_lifecycle = self.producer is None
                if should_manage_lifecycle:
                    await producer.start()
                try:
                    send_kwargs: dict[str, Any] = {
                        "key": message.task_id.encode("utf-8"),
                    }
                    headers = _trace_headers(message.traceparent or current_traceparent())
                    if headers:
                        send_kwargs["headers"] = headers
                    await producer.send_and_wait(
                        self.topic,
                        message.model_dump_json(exclude_none=True).encode("utf-8"),
                        **send_kwargs,
                    )
                    metric_status = "published"
                finally:
                    if should_manage_lifecycle:
                        await producer.stop()
        finally:
            record_delivery_event(
                "task_publish",
                "kafka",
                metric_status,
                time.perf_counter() - start,
            )


@dataclass(slots=True)
class FileSpoolDeliveryResultConsumer:
    """Local file-spool reader for ``delivery.results.v1`` result events."""

    base_dir: Path | str
    result_topic: str = "delivery.results.v1"
    _result_dir: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._result_dir = Path(self.base_dir) / self.result_topic

    async def consume_records(self) -> list[DeliveryResultRecord]:
        return [
            DeliveryResultRecord(message=message, ack=_noop_ack)
            for message in await self.consume()
        ]

    async def consume(self) -> list[DeliveryResultMessage]:
        if not self._result_dir.exists():
            return []

        messages: list[DeliveryResultMessage] = []
        for path in sorted(self._result_dir.glob("*.json")):
            messages.append(DeliveryResultMessage.model_validate_json(
                path.read_text(encoding="utf-8")
            ))
        return messages


@dataclass(slots=True)
class KafkaDeliveryResultConsumer:
    """Kafka consumer for ``delivery.results.v1`` result events."""

    bootstrap_servers: str
    topic: str = "delivery.results.v1"
    group_id: str = "control-plane-results"
    max_records: int = 100
    timeout_ms: int = 1000
    consumer: Any | None = None
    _owned_consumer: Any | None = field(default=None, init=False, repr=False)

    async def consume_records(self) -> list[DeliveryResultRecord]:
        consumer = self.consumer or AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        should_manage_lifecycle = self.consumer is None
        if should_manage_lifecycle:
            await consumer.start()
            self._owned_consumer = consumer
        batches = await consumer.getmany(
            timeout_ms=self.timeout_ms,
            max_records=self.max_records,
        )
        records: list[DeliveryResultRecord] = []
        for topic_partition, messages in batches.items():
            for kafka_message in messages:
                result = DeliveryResultMessage.model_validate_json(
                    kafka_message.value.decode("utf-8")
                )
                offset = kafka_message.offset + 1

                async def _ack(
                    tp=topic_partition,
                    committed_offset=offset,
                ) -> None:
                    await consumer.commit({tp: committed_offset})

                records.append(DeliveryResultRecord(message=result, ack=_ack))
        return records

    async def close(self) -> None:
        if self._owned_consumer is not None:
            await self._owned_consumer.stop()
            self._owned_consumer = None


async def _noop_ack() -> None:
    return None


async def consume_delivery_results(
    session: AsyncSession,
    consumer: Any,
) -> list[DeliveryResultApplySummary]:
    """Read pending local result messages and apply them in order."""
    start = time.perf_counter()
    metric_status = "error"
    transport = _consumer_transport(consumer)
    summaries: list[DeliveryResultApplySummary] = []
    lease_client = create_redis_lease()
    try:
        with start_trace_span(
            "control_plane.delivery.result_consume",
            attributes={
                "delivery.operation": "result_consume",
                "delivery.transport": transport,
            },
        ) as span:
            if hasattr(consumer, "consume_records"):
                records = await consumer.consume_records()
            else:
                records = [
                    DeliveryResultRecord(message=message, ack=_noop_ack)
                    for message in await consumer.consume()
                ]
            if span is not None:
                span.set_attribute("delivery.result.records", len(records))
            for record in records:
                lease = await _acquire_result_apply_lease(lease_client, record.message)
                if not lease.acquired:
                    continue
                try:
                    summaries.append(await apply_delivery_result(session, record.message))
                    await record.ack()
                finally:
                    await lease_client.release(lease)
            if span is not None:
                span.set_attribute("delivery.result.applied_records", len(summaries))
            metric_status = "consumed" if records else "empty"
            return summaries
    finally:
        record_delivery_event(
            "result_consume",
            transport,
            metric_status,
            time.perf_counter() - start,
        )
        await lease_client.aclose()
        if hasattr(consumer, "close"):
            await consumer.close()


def _consumer_transport(consumer: Any) -> str:
    if isinstance(consumer, KafkaDeliveryResultConsumer):
        return "kafka"
    if isinstance(consumer, FileSpoolDeliveryResultConsumer):
        return "file"
    return "unknown"


def _trace_headers(traceparent: str | None) -> list[tuple[str, bytes]] | None:
    if not traceparent:
        return None
    return [("traceparent", traceparent.encode("ascii"))]


async def _acquire_result_apply_lease(
    lease_client,
    message: DeliveryResultMessage,
) -> LeaseClaim:
    return await lease_client.acquire(f"delivery_result_apply:{message.task_id}")
