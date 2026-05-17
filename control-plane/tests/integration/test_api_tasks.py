from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _make_zip_bytes(files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (files or {"test.txt": b"hello"}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mock_task(
    task_id: str = "abc123",
    status: str = "draft",
    idempotency_key: str = "idem-1",
    submission_label: str = "test.zip",
    temp_dir: str = "/tmp/auto_upload_tasks/abc123",
    summary_json: dict | None = None,
    created_by: str = "local-user",
    created_at: datetime | None = None,
    confirmed_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.status = status
    t.idempotency_key = idempotency_key
    t.submission_label = submission_label
    t.temp_dir = temp_dir
    t.summary_json = summary_json or {}
    t.created_by = created_by
    t.created_at = created_at or datetime(2026, 1, 1, tzinfo=UTC)
    t.confirmed_at = confirmed_at
    t.finished_at = finished_at
    return t


def _mock_item(
    item_id: str = "item01",
    task_id: str = "abc123",
    src_path: str = "test.txt",
    filename: str = "test.txt",
    severity: str = "ok",
    upload_status: str = "pending",
) -> MagicMock:
    i = MagicMock()
    i.id = item_id
    i.task_id = task_id
    i.src_path = src_path
    i.filename = filename
    i.ext = ".txt"
    i.file_size = 5
    i.target_name_raw = "target"
    i.target_name_matched = "target"
    i.document_type = "report"
    i.category_name = "finance"
    i.dst_dir = "finance/report"
    i.dst_path = "finance/report/test.txt"
    i.severity = severity
    i.error_code = ""
    i.error_message = ""
    i.warning_message = ""
    i.upload_status = upload_status
    i.upload_error = ""
    i.uploaded_at = None
    return i


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return session


@pytest.fixture
def async_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Test 1: GET /healthz ─────────────────────────────────────────────────────

async def test_healthz(async_client):
    async with async_client as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["service"] == "control-plane"


# ── Test 2: GET /api/v1/tasks — empty list ───────────────────────────────────

async def test_list_tasks_empty(async_client):
    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks.get_session") as mock_get_session,
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_get_session.return_value = mock_session

        async def override_session():
            yield mock_session

        app.dependency_overrides[
            __import__("app.core.db", fromlist=["get_session"]).get_session
        ] = override_session
        mock_repo.list = AsyncMock(return_value=[])

        async with async_client as client:
            resp = await client.get("/api/v1/tasks")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["tasks"] == []


# ── Test 3: GET /api/v1/tasks/{id} — task not found ─────────────────────────

async def test_get_task_not_found(async_client):
    from app.core.db import get_session

    async def override_session():
        mock = AsyncMock()
        mock.flush = AsyncMock()
        mock.commit = AsyncMock()
        yield mock

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get = AsyncMock(return_value=None)
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get("/api/v1/tasks/notexist")

        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ── Test 4: GET /api/v1/tasks/{id} — task found ──────────────────────────────

async def test_get_task_found(async_client):
    from app.core.db import get_session

    task = _mock_task()

    async def override_session():
        mock = AsyncMock()
        yield mock

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get = AsyncMock(return_value=task)
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get("/api/v1/tasks/abc123")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "abc123"
    assert data["status"] == "draft"


# ── Test 5: POST /api/v1/tasks — file too large ──────────────────────────────

async def test_create_task_file_too_large(async_client):
    from app.core.db import get_session

    big_bytes = b"x" * 10

    with patch("app.api.tasks.get_settings") as mock_settings_fn:
        mock_s = MagicMock()
        mock_s.max_zip_bytes = 5
        mock_s.task_dir_base = "/tmp/test_tasks"
        mock_settings_fn.return_value = mock_s

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files={"file": ("big.zip", big_bytes, "application/zip")},
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 413


# ── Test 6: POST /api/v1/tasks — idempotency returns existing ────────────────

async def test_create_task_idempotent(async_client):
    from app.core.db import get_session

    existing = _mock_task(task_id="exist01", status="draft", idempotency_key="idem-x")
    zip_bytes = _make_zip_bytes()

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get_by_idempotency_key = AsyncMock(return_value=existing)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                data={"idempotency_key": "idem-x"},
                files={"file": ("test.zip", zip_bytes, "application/zip")},
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "exist01"


async def test_upload_task_queues_delivery_message_when_go_worker_backend(async_client, tmp_path):
    from app.core.db import get_session

    task = _mock_task(status="confirmed")
    item = _mock_item()

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks._task_repo") as mock_task_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_settings = MagicMock()
        mock_settings.delivery_backend = "go-worker"
        mock_settings.delivery_transport = "file"
        mock_settings.delivery_outbox_base = str(tmp_path)
        mock_settings.s3_bucket_name = "auto-upload-dev"
        mock_settings.task_dir_base = str(tmp_path)
        mock_settings_fn.return_value = mock_settings

        mock_task_repo.get = AsyncMock(return_value=task)
        mock_task_repo.update_status = AsyncMock(return_value=task)
        mock_item_repo.list_by_task = AsyncMock(return_value=[item])
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    outbox_file = tmp_path / "delivery.tasks.v1" / "abc123.json"
    assert outbox_file.exists()
    payload = json.loads(outbox_file.read_text(encoding="utf-8"))
    assert payload["task_id"] == "abc123"
    assert payload["bucket_name"] == "auto-upload-dev"
    assert len(payload["items"]) == 1


async def test_upload_task_publishes_to_kafka_when_configured(async_client):
    from app.core.db import get_session

    task = _mock_task(status="confirmed")
    item = _mock_item()

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks.KafkaDeliveryPublisher") as publisher_cls,
        patch("app.api.tasks._task_repo") as mock_task_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_settings = MagicMock()
        mock_settings.delivery_backend = "go-worker"
        mock_settings.delivery_transport = "kafka"
        mock_settings.kafka_bootstrap_servers = "localhost:9092"
        mock_settings.kafka_task_topic = "delivery.tasks.v1"
        mock_settings.s3_bucket_name = "auto-upload-dev"
        mock_settings_fn.return_value = mock_settings

        publisher = AsyncMock()
        publisher_cls.return_value = publisher
        mock_task_repo.get = AsyncMock(return_value=task)
        mock_task_repo.update_status = AsyncMock(return_value=task)
        mock_item_repo.list_by_task = AsyncMock(return_value=[item])
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    publisher_cls.assert_called_once_with(
        bootstrap_servers="localhost:9092",
        topic="delivery.tasks.v1",
    )
    publisher.publish.assert_awaited_once()
    mock_event_repo.append.assert_awaited_once()
    assert mock_event_repo.append.await_args.args[3]["transport"] == "kafka"


async def test_upload_task_can_publish_object_source_reference(async_client, tmp_path):
    from app.core.db import get_session
    from app.services.delivery import DeliverySourceReference

    task = _mock_task(status="confirmed", temp_dir=str(tmp_path / "abc123"))
    item = _mock_item()

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks.stage_task_archive") as stage_task_archive,
        patch("app.api.tasks._task_repo") as mock_task_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_settings = MagicMock()
        mock_settings.delivery_backend = "go-worker"
        mock_settings.delivery_transport = "file"
        mock_settings.delivery_source_mode = "object"
        mock_settings.delivery_outbox_base = str(tmp_path)
        mock_settings.task_dir_base = str(tmp_path)
        mock_settings.s3_bucket_name = "auto-upload-dev"
        mock_settings.staging_bucket_name = "auto-upload-staging"
        mock_settings_fn.return_value = mock_settings

        stage_task_archive.return_value = DeliverySourceReference(
            bucket="auto-upload-staging",
            key="staged/tasks/abc123/archive.zip",
            sha256="abc",
            size=123,
        )
        mock_task_repo.get = AsyncMock(return_value=task)
        mock_task_repo.update_status = AsyncMock(return_value=task)
        mock_item_repo.list_by_task = AsyncMock(return_value=[item])
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    stage_task_archive.assert_awaited_once_with(
        task,
        bucket_name="auto-upload-staging",
    )
    payload = json.loads(
        (tmp_path / "delivery.tasks.v1" / "abc123.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 2
    assert payload["source"]["bucket"] == "auto-upload-staging"
    assert payload["source"]["key"] == "staged/tasks/abc123/archive.zip"
    assert payload["items"][0]["source_path"] == "test.txt"


# ── Test 7: POST /api/v1/tasks — new task created ────────────────────────────

async def test_create_task_new(async_client, tmp_path):
    from app.core.db import get_session

    created_task = _mock_task(
        task_id="new001",
        status="draft",
        temp_dir=str(tmp_path / "new001"),
    )
    zip_bytes = _make_zip_bytes()

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks.get_settings") as mock_settings_fn,
    ):
        mock_s = MagicMock()
        mock_s.max_zip_bytes = 524_288_000
        mock_s.task_dir_base = str(tmp_path)
        mock_settings_fn.return_value = mock_s

        mock_repo.get_by_idempotency_key = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(return_value=created_task)
        mock_repo.update_status = AsyncMock(return_value=created_task)

        mock_session_obj = AsyncMock()
        mock_session_obj.flush = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        updated_task = _mock_task(
            task_id="new001",
            status="draft",
            temp_dir=str(tmp_path / "new001"),
        )
        mock_repo.get = AsyncMock(return_value=updated_task)

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files={"file": ("test.zip", zip_bytes, "application/zip")},
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 201
    data = resp.json()
    assert data["task_id"] == "new001"


# ── Test 8: POST /api/v1/tasks/{id}/confirm ──────────────────────────────────

async def test_confirm_task(async_client):
    from app.core.db import get_session

    task = _mock_task(status="classified")
    confirmed_task = _mock_task(status="confirmed")

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get = AsyncMock(return_value=task)
        mock_repo.update_status = AsyncMock(return_value=confirmed_task)

        mock_session_obj = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/confirm")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "confirmed"


# ── Test 9: POST /api/v1/tasks/{id}/upload — wrong status ────────────────────

async def test_upload_task_wrong_status(async_client):
    from app.core.db import get_session

    task = _mock_task(status="draft")

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get = AsyncMock(return_value=task)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 422


# ── Test 10: POST /api/v1/tasks/{id}/upload — confirmed task ─────────────────

async def test_upload_task_confirmed(async_client):
    from app.core.db import get_session

    task = _mock_task(status="confirmed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks.run_task") as mock_run_task,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_run_task.return_value = None

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "uploading"


# ── Test 11: POST /api/v1/tasks/{id}/retry ───────────────────────────────────

async def test_retry_task(async_client):
    from app.core.db import get_session

    task = _mock_task(status="partial_failed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_item_repo.batch_reset_failed = AsyncMock(return_value=3)

        mock_session_obj = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/retry")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["reset_count"] == 3


# ── Test 12: GET /api/v1/tasks/{id}/preview ──────────────────────────────────

async def test_preview_task(async_client):
    from app.core.db import get_session

    task = _mock_task(status="classified", summary_json={"total": 1, "ok": 1})
    item = _mock_item()

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_item_repo.list_by_task = AsyncMock(return_value=[item])

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get("/api/v1/tasks/abc123/preview")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "abc123"
    assert len(data["items"]) == 1
    assert data["items"][0]["filename"] == "test.txt"


# ── Test 13: GET /api/v1/tasks/{id}/progress — SSE stream ───────────────────

async def test_progress_sse(async_client):
    import asyncio

    from app.api.tasks import init_progress_bus
    from app.services.progress_bus import ProgressBus

    bus = ProgressBus()
    init_progress_bus(bus)

    async def publish_and_close():
        await asyncio.sleep(0.05)
        await bus.publish("stream01", {"type": "test_event", "value": 42})
        await bus.close_task("stream01")

    with patch("app.api.tasks.get_bus", return_value=bus):
        task = asyncio.create_task(publish_and_close())

        async with async_client as client:
            resp = await client.get(
                "/api/v1/tasks/stream01/progress",
                headers={"Accept": "text/event-stream"},
            )
            content = resp.content.decode()

        await task

    assert "test_event" in content
    assert "42" in content


# ── Test 14: POST /api/v1/tasks/{id}/classify — profile not found ────────────

async def test_classify_profile_not_found(async_client):
    from app.core.db import get_session

    task = _mock_task(status="draft", temp_dir="/tmp/nonexistent_task_dir")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks.get_settings") as mock_settings_fn,
    ):
        mock_s = MagicMock()
        mock_s.classification_profile_path = "/nonexistent/profile.json"
        mock_settings_fn.return_value = mock_s

        mock_repo.get = AsyncMock(return_value=task)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/classify")

        app.dependency_overrides.clear()

    assert resp.status_code in (422, 500)


# ── Test 15: GET /api/v1/tasks with pagination ───────────────────────────────

async def test_list_tasks_pagination(async_client):
    from app.core.db import get_session

    tasks = [_mock_task(task_id=f"t{i:02d}") for i in range(3)]

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.list = AsyncMock(return_value=tasks)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get("/api/v1/tasks?limit=10&offset=0")

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 3
    assert data["limit"] == 10
    assert data["offset"] == 0
