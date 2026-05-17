from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    traceparent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_delivery_task_message(
    *,
    task,
    upload_items: list[Any],
    bucket_name: str,
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
        task_id=task.id,
        idempotency_key=task.idempotency_key,
        submission_label=task.submission_label,
        temp_dir=task.temp_dir,
        bucket_name=bucket_name,
        items=items,
        traceparent=traceparent,
        metadata=metadata,
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
        self._topic_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._topic_dir / f"{message.task_id}.json.tmp"
        final_path = self._topic_dir / f"{message.task_id}.json"
        tmp_path.write_text(
            message.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        tmp_path.replace(final_path)
        return final_path
