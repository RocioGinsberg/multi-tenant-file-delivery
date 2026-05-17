from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.delivery import (
    FileSpoolDeliveryPublisher,
    build_delivery_task_message,
)


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
