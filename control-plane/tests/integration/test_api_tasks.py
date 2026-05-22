from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.main import app


def _mock_task(
    task_id: str = "abc123",
    status: str = "draft",
    idempotency_key: str = "idem-1",
    submission_label: str = "test.zip",
    temp_dir: str = "/tmp/auto_upload_tasks/abc123",
    summary_json: dict | None = None,
    created_by: str = "local-user",
    owner_tenant_id: str = "hq",
    owner_user_id: str = "local-user",
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
    t.owner_tenant_id = owner_tenant_id
    t.owner_user_id = owner_user_id
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
    assert data["checks"]["redis"] == "disabled"


async def test_healthz_checks_redis_when_enabled(async_client):
    settings = MagicMock()
    settings.env = "test"
    settings.redis_healthcheck_enabled = True

    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(return_value=True)
    redis_client.close = AsyncMock()

    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main.create_redis_client", return_value=redis_client) as create_client,
    ):
        async with async_client as client:
            resp = await client.get("/healthz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["redis"] == "ok"
    create_client.assert_called_once_with(settings)
    redis_client.ping.assert_awaited_once()
    redis_client.close.assert_awaited_once()


async def test_metrics_endpoint_disabled_by_default(async_client):
    settings = MagicMock()
    settings.metrics_enabled = False

    with patch("app.services.metrics.get_settings", return_value=settings):
        async with async_client as client:
            resp = await client.get("/metrics")

    assert resp.status_code == 404


async def test_metrics_endpoint_records_http_requests_when_enabled(async_client):
    settings = MagicMock()
    settings.metrics_enabled = True

    with patch("app.services.metrics.get_settings", return_value=settings):
        async with async_client as client:
            health_resp = await client.get("/healthz")
            missing_resp = await client.get("/missing/tenant-specific-resource")
            metrics_resp = await client.get("/metrics")

    assert health_resp.status_code == 200
    assert missing_resp.status_code == 404
    assert metrics_resp.status_code == 200
    assert "text/plain" in metrics_resp.headers["content-type"]
    body = metrics_resp.text
    assert "control_plane_http_requests_total" in body
    assert 'route="/healthz"' in body
    assert 'route="unmatched"' in body
    assert "/missing/tenant-specific-resource" not in body


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
    assert data["owner_tenant_id"] == "hq"
    assert data["owner_user_id"] == "local-user"


async def test_get_task_uses_actor_tenant_filter(async_client):
    from app.core.db import get_session

    task = _mock_task(owner_tenant_id="subsidiary-a", owner_user_id="sub-user")

    async def override_session():
        mock = AsyncMock()
        yield mock

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get = AsyncMock(return_value=task)
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get(
                "/api/v1/tasks/abc123",
                headers={
                    "X-Actor-Tenant": "subsidiary-a",
                    "X-Actor-User": "sub-user",
                    "X-Actor-Role": "subsidiary_viewer",
                },
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert mock_repo.get.await_args.kwargs["tenant_id"] == "subsidiary-a"


# ── Test 5: POST /api/v1/tasks — folder payload too large ───────────────────

async def test_create_task_folder_payload_too_large(async_client):
    from app.core.db import get_session

    big_bytes = b"x" * 10

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks._task_repo") as mock_repo,
    ):
        mock_s = MagicMock()
        mock_s.max_internal_archive_bytes = 5
        mock_s.task_dir_base = "/tmp/test_tasks"
        mock_settings_fn.return_value = mock_s
        mock_repo.get_by_idempotency_key = AsyncMock(return_value=None)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files=[("files", ("acme/big.txt", big_bytes, "text/plain"))],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 413


# ── Test 6: POST /api/v1/tasks — idempotency returns existing ────────────────

async def test_create_task_idempotent(async_client):
    from app.core.db import get_session

    existing = _mock_task(task_id="exist01", status="draft", idempotency_key="idem-x")

    with patch("app.api.tasks._task_repo") as mock_repo:
        mock_repo.get_by_idempotency_key = AsyncMock(return_value=existing)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                data={"idempotency_key": "idem-x"},
                files=[("files", ("acme/test.txt", b"hello", "text/plain"))],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "exist01"


async def test_create_task_returns_conflict_when_idempotency_claim_exists(async_client):
    from app.core.db import get_session

    with patch("app.api.tasks._acquire_idempotency_claim") as acquire_claim:
        acquire_claim.side_effect = HTTPException(
            status_code=409,
            detail="create_task for 'idem-busy' is already in progress",
        )

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                data={"idempotency_key": "idem-busy"},
                files=[("files", ("acme/test.txt", b"hello", "text/plain"))],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]


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
    assert mock_event_repo.append.await_count == 2
    staged_event = mock_event_repo.append.await_args_list[0]
    assert staged_event.args[2] == "task_staged_source"
    assert staged_event.args[3] == {
        "actor_tenant_id": "hq",
        "actor_user_id": "local-user",
        "actor_role": "hq_uploader",
        "type": "object",
        "bucket": "auto-upload-staging",
        "key": "staged/tasks/abc123/archive.zip",
        "sha256": "abc",
        "size": 123,
    }


async def test_upload_task_deletes_staged_source_when_publish_fails():
    from app.core.db import get_session
    from app.services.delivery import DeliverySourceReference

    task = _mock_task(status="confirmed")
    item = _mock_item()
    source_ref = DeliverySourceReference(
        bucket="auto-upload-staging",
        key="staged/tasks/abc123/archive.zip",
        sha256="abc",
        size=123,
    )

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks.stage_task_archive", new_callable=AsyncMock) as stage_task_archive,
        patch(
            "app.api.tasks.delete_staged_archive",
            new_callable=AsyncMock,
        ) as delete_staged_archive,
        patch("app.api.tasks.FileSpoolDeliveryPublisher") as publisher_cls,
        patch("app.api.tasks._task_repo") as mock_task_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_settings = MagicMock()
        mock_settings.delivery_backend = "go-worker"
        mock_settings.delivery_transport = "file"
        mock_settings.delivery_source_mode = "object"
        mock_settings.delivery_outbox_base = "/tmp/outbox"
        mock_settings.task_dir_base = "/tmp/tasks"
        mock_settings.s3_bucket_name = "auto-upload-dev"
        mock_settings.staging_bucket_name = "auto-upload-staging"
        mock_settings_fn.return_value = mock_settings

        publisher = AsyncMock()
        publisher.publish.side_effect = RuntimeError("publish failed")
        publisher_cls.return_value = publisher
        stage_task_archive.return_value = source_ref
        mock_task_repo.get = AsyncMock(return_value=task)
        mock_item_repo.list_by_task = AsyncMock(return_value=[item])
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        mock_session_obj = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 500
    delete_staged_archive.assert_awaited_once_with(source_ref)
    assert mock_session_obj.commit.await_count == 2
    deleted_event = mock_event_repo.append.await_args_list[-1]
    assert deleted_event.args[2] == "task_staged_source_deleted"
    assert deleted_event.args[3]["reason"] == "publish_failed"


# ── Test 7: POST /api/v1/tasks — new task created ────────────────────────────

async def test_create_task_new(async_client, tmp_path):
    from app.core.db import get_session

    created_task = _mock_task(
        task_id="new001",
        status="draft",
        temp_dir=str(tmp_path / "new001"),
    )
    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
        patch("app.api.tasks.get_settings") as mock_settings_fn,
    ):
        mock_s = MagicMock()
        mock_s.max_internal_archive_bytes = 524_288_000
        mock_s.max_folder_payload_bytes = 1_073_741_824
        mock_s.max_file_count = 5000
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
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files=[("files", ("acme/test.txt", b"hello", "text/plain"))],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 201
    data = resp.json()
    assert data["task_id"] == "new001"
    assert mock_repo.create.await_args.kwargs["owner_tenant_id"] == "hq"
    assert mock_repo.create.await_args.kwargs["owner_user_id"] == "local-user"


async def test_create_task_rejects_subsidiary_viewer(async_client):
    from app.core.db import get_session

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_session] = override_session
    async with async_client as client:
        resp = await client.post(
            "/api/v1/tasks",
            headers={
                "X-Actor-Tenant": "subsidiary-a",
                "X-Actor-User": "sub-user",
                "X-Actor-Role": "subsidiary_viewer",
            },
            files=[("files", ("acme/test.txt", b"hello", "text/plain"))],
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 403


async def test_create_task_accepts_folder_files(async_client, tmp_path):
    from app.core.db import get_session

    created_task = _mock_task(
        task_id="folder01",
        status="draft",
        temp_dir=str(tmp_path / "folder01"),
    )

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
        patch("app.api.tasks.get_settings") as mock_settings_fn,
    ):
        mock_s = MagicMock()
        mock_s.max_internal_archive_bytes = 524_288_000
        mock_s.max_folder_payload_bytes = 1_073_741_824
        mock_s.max_file_count = 5000
        mock_s.task_dir_base = str(tmp_path)
        mock_settings_fn.return_value = mock_s

        mock_repo.get_by_idempotency_key = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(return_value=created_task)
        mock_repo.get = AsyncMock(return_value=created_task)
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        mock_session_obj = AsyncMock()
        mock_session_obj.flush = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                data={"submission_label": "HQ batch"},
                files=[
                    ("files", ("acme/report.txt", b"hello", "text/plain")),
                    ("files", ("globex/report.txt", b"world", "text/plain")),
                ],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 201
    mock_repo.create.assert_awaited_once()
    assert mock_repo.create.await_args.kwargs["submission_label"] == "HQ batch"
    assert (tmp_path / "folder01" / ".auto_upload_internal" / "original.zip").exists()
    assert (tmp_path / "folder01" / "acme" / "report.txt").read_text(encoding="utf-8") == "hello"
    assert (tmp_path / "folder01" / "globex" / "report.txt").read_text(encoding="utf-8") == "world"


async def test_create_task_keeps_user_original_zip_separate(async_client, tmp_path):
    from app.core.db import get_session

    created_task = _mock_task(
        task_id="folder02",
        status="draft",
        temp_dir=str(tmp_path / "folder02"),
    )

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
        patch("app.api.tasks.get_settings") as mock_settings_fn,
    ):
        mock_s = MagicMock()
        mock_s.max_internal_archive_bytes = 524_288_000
        mock_s.max_folder_payload_bytes = 1_073_741_824
        mock_s.max_file_count = 5000
        mock_s.task_dir_base = str(tmp_path)
        mock_settings_fn.return_value = mock_s

        mock_repo.get_by_idempotency_key = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(return_value=created_task)
        mock_repo.get = AsyncMock(return_value=created_task)
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

        mock_session_obj = AsyncMock()
        mock_session_obj.flush = AsyncMock()
        mock_session_obj.commit = AsyncMock()

        async def override_session():
            yield mock_session_obj

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files=[
                    ("files", ("original.zip", b"user zip bytes", "application/zip")),
                    ("files", ("acme/report.txt", b"hello", "text/plain")),
                ],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert (tmp_path / "folder02" / "original.zip").read_bytes() == b"user zip bytes"
    internal_archive = tmp_path / "folder02" / ".auto_upload_internal" / "original.zip"
    assert internal_archive.exists()
    assert internal_archive.read_bytes() != b"user zip bytes"


async def test_create_task_rejects_folder_file_count_limit(async_client):
    from app.core.db import get_session

    with (
        patch("app.api.tasks.get_settings") as mock_settings_fn,
        patch("app.api.tasks._task_repo") as mock_repo,
    ):
        mock_s = MagicMock()
        mock_s.max_internal_archive_bytes = 524_288_000
        mock_s.max_folder_payload_bytes = 1_073_741_824
        mock_s.max_file_count = 1
        mock_s.task_dir_base = "/tmp/test_tasks"
        mock_settings_fn.return_value = mock_s
        mock_repo.get_by_idempotency_key = AsyncMock(return_value=None)

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/tasks",
                files=[
                    ("files", ("acme/one.txt", b"one", "text/plain")),
                    ("files", ("acme/two.txt", b"two", "text/plain")),
                ],
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 413


# ── Test 8: POST /api/v1/tasks/{id}/confirm ──────────────────────────────────

async def test_confirm_task(async_client):
    from app.core.db import get_session

    task = _mock_task(status="classified")
    confirmed_task = _mock_task(status="confirmed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_repo.update_status = AsyncMock(return_value=confirmed_task)
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

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
    mock_repo.update_status.assert_awaited_once()
    assert mock_repo.update_status.await_args.kwargs["tenant_id"] == "hq"
    event_args = mock_event_repo.append.await_args.args
    assert event_args[2] == "confirmed"
    assert event_args[3] == {
        "actor_tenant_id": "hq",
        "actor_user_id": "local-user",
        "actor_role": "hq_uploader",
    }


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


async def test_upload_task_rejects_subsidiary_viewer(async_client):
    from app.core.db import get_session

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_session] = override_session
    async with async_client as client:
        resp = await client.post(
            "/api/v1/tasks/abc123/upload",
            headers={
                "X-Actor-Tenant": "subsidiary-a",
                "X-Actor-User": "sub-user",
                "X-Actor-Role": "subsidiary_viewer",
            },
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 403


# ── Test 10: POST /api/v1/tasks/{id}/upload — confirmed task ─────────────────

async def test_upload_task_confirmed(async_client):
    from app.core.db import get_session

    task = _mock_task(status="confirmed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
        patch("app.api.tasks.run_task") as mock_run_task,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_event_repo.append = AsyncMock(return_value=MagicMock())
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
    event_args = mock_event_repo.append.await_args.args
    assert event_args[2] == "task_upload_requested"
    assert event_args[3] == {
        "actor_tenant_id": "hq",
        "actor_user_id": "local-user",
        "actor_role": "hq_uploader",
    }


async def test_upload_task_returns_conflict_when_idempotency_claim_exists(async_client):
    from app.core.db import get_session

    task = _mock_task(status="confirmed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._acquire_idempotency_claim") as acquire_claim,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        acquire_claim.side_effect = HTTPException(
            status_code=409,
            detail="upload_task for 'abc123' is already in progress",
        )

        async def override_session():
            yield AsyncMock()

        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post("/api/v1/tasks/abc123/upload")

        app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]


# ── Test 11: POST /api/v1/tasks/{id}/retry ───────────────────────────────────

async def test_retry_task(async_client):
    from app.core.db import get_session

    task = _mock_task(status="partial_failed")

    with (
        patch("app.api.tasks._task_repo") as mock_repo,
        patch("app.api.tasks._item_repo") as mock_item_repo,
        patch("app.api.tasks._event_repo") as mock_event_repo,
    ):
        mock_repo.get = AsyncMock(return_value=task)
        mock_item_repo.batch_reset_failed = AsyncMock(return_value=3)
        mock_event_repo.append = AsyncMock(return_value=MagicMock())

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
    assert data["status"] == "confirmed"
    assert task.status == "confirmed"
    assert mock_item_repo.batch_reset_failed.await_args.kwargs["tenant_id"] == "hq"


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

    with (
        patch("app.api.tasks.get_bus", return_value=bus),
        patch("app.api.tasks._task_repo") as mock_repo,
    ):
        mock_repo.get = AsyncMock(return_value=_mock_task(task_id="stream01"))
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
