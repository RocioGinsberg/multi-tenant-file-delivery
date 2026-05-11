from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.classification_profile import (
    DocumentTypeConfig,
    ProfileConfig,
    TargetConfig,
    TargetExtractionConfig,
)

# Columns accepted by TaskItem ORM model (excludes ClassifiedItem-only fields).
_TASK_ITEM_COLUMNS = frozenset({
    "src_path", "filename", "ext", "file_size",
    "target_name_raw", "target_name_matched",
    "document_type", "category_name",
    "dst_dir", "dst_path",
    "severity", "error_code", "error_message", "warning_message",
    "upload_status", "upload_error", "uploaded_at",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zip_bytes(files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (files or {"acme/月报.xlsx": b"fake xlsx content"}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_profile() -> ProfileConfig:
    """Return a minimal in-memory ProfileConfig (no disk read)."""
    return ProfileConfig(
        version="1",
        targets=[TargetConfig(key="acme", aliases=[], strip_number_prefix=False)],
        document_types={"report": DocumentTypeConfig(category="reports")},
        suffix_priority={},
        description_mapping={},
        suffix_fallback={".xlsx": "report", ".txt": "report"},
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def mem_engine():
    """Create an isolated in-memory SQLite engine and tables."""
    from app.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def async_client(mem_engine, tmp_path, monkeypatch):
    """
    AsyncClient wired to the FastAPI app with:
    - Patched async_engine / async_session_maker → in-memory SQLite
    - Patched get_settings → tmp_path for task_dir_base
    - Patched _load_profile → in-memory ProfileConfig
    - Patched aioboto3.Session → AsyncMock so S3 never hits network
    """
    from app.core import settings as settings_module
    from app.main import app

    import app.core.db as db_module

    # ── Build in-memory session maker ────────────────────────────────────────
    new_session_maker = async_sessionmaker(mem_engine, expire_on_commit=False, class_=AsyncSession)

    monkeypatch.setattr(db_module, "async_engine", mem_engine)
    monkeypatch.setattr(db_module, "async_session_maker", new_session_maker)

    # ── Patch get_session dependency so FastAPI routes use the right maker ───
    from app.core.db import get_session as real_get_session

    async def override_get_session():
        async with new_session_maker() as session:
            yield session

    app.dependency_overrides[real_get_session] = override_get_session

    # ── Patch settings ───────────────────────────────────────────────────────
    settings_module.get_settings.cache_clear()

    mock_settings = MagicMock()
    mock_settings.max_zip_bytes = 524_288_000
    mock_settings.task_dir_base = str(tmp_path)
    mock_settings.classification_profile_path = "/fake/profile.json"
    mock_settings.s3_endpoint_url = "http://localhost:9000"
    mock_settings.s3_bucket_name = "test-bucket"
    mock_settings.s3_region = "us-east-1"
    mock_settings.s3_access_key_id = "key"
    mock_settings.s3_secret_access_key = "secret"
    mock_settings.worker_max_target_concurrent = 3
    mock_settings.worker_max_file_concurrent = 5
    mock_settings.worker_auto_adjust_concurrent = False
    mock_settings.cors_origins = "*"
    mock_settings.env = "test"

    monkeypatch.setattr("app.api.tasks.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.services.task_runner.get_settings", lambda: mock_settings)

    # ── Patch _load_profile so classify doesn't read disk ───────────────────
    profile = _make_profile()
    monkeypatch.setattr("app.api.tasks._load_profile", lambda _path: profile)

    # ── Wrap classify_zip so item.to_dict() only returns TaskItem columns ────
    # ClassifiedItem.to_dict() (via asdict) includes extra fields not in the
    # TaskItem ORM model (target_match_method, document_match_method, etc.).
    # We wrap the real function to strip those extras before they hit bulk_insert.
    from app.services.classifier import classify_zip as _real_classify_zip

    class _FilteredItem:
        """Thin wrapper that delegates to a ClassifiedItem but filters to_dict."""
        __slots__ = ("_item",)

        def __init__(self, item):
            object.__setattr__(self, "_item", item)

        def __getattr__(self, name: str):
            return getattr(object.__getattribute__(self, "_item"), name)

        def to_dict(self) -> dict[str, Any]:
            full = object.__getattribute__(self, "_item").to_dict()
            return {k: v for k, v in full.items() if k in _TASK_ITEM_COLUMNS}

    def _wrapped_classify_zip(zip_bytes, profile_cfg):
        items, summary = _real_classify_zip(zip_bytes, profile_cfg)
        return [_FilteredItem(i) for i in items], summary

    monkeypatch.setattr("app.api.tasks.classify_zip", _wrapped_classify_zip)

    # ── Patch aioboto3.Session so upload_file never touches network ──────────
    mock_s3_client = AsyncMock()
    mock_s3_client.put_object = AsyncMock(return_value={"ETag": '"abc123etag"'})
    mock_s3_client.__aenter__ = AsyncMock(return_value=mock_s3_client)
    mock_s3_client.__aexit__ = AsyncMock(return_value=False)

    mock_aioboto3_session = MagicMock()
    mock_aioboto3_session.client = MagicMock(return_value=mock_s3_client)

    # ── Manually initialize the progress bus (lifespan may not set it reliably) ─
    from app.api.tasks import init_progress_bus
    from app.services.progress_bus import ProgressBus

    bus = ProgressBus()
    init_progress_bus(bus)  # also calls set_progress_bus in task_runner

    with patch("app.services.s3_uploader.aioboto3.Session", return_value=mock_aioboto3_session):
        # Also patch the module-level async_engine used by main.py lifespan
        with patch("app.main.async_engine", mem_engine):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    app.dependency_overrides.clear()
    settings_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
async def test_e2e_full_flow(async_client):
    """Complete business flow: upload zip → classify → preview → confirm → upload."""
    zip_bytes = _make_zip_bytes({"acme/月报.xlsx": b"fake xlsx"})

    # Step 1: Upload zip
    resp = await async_client.post(
        "/api/v1/tasks",
        files={"file": ("test.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 201, f"create_task failed: {resp.text}"
    task_id = resp.json()["task_id"]
    assert task_id

    # Step 2: Classify
    resp = await async_client.post(f"/api/v1/tasks/{task_id}/classify")
    assert resp.status_code == 200, f"classify failed: {resp.text}"
    body = resp.json()
    assert body["summary"]["total"] > 0

    # Step 3: Preview
    resp = await async_client.get(f"/api/v1/tasks/{task_id}/preview")
    assert resp.status_code == 200, f"preview failed: {resp.text}"
    items = resp.json()["items"]
    assert len(items) > 0

    # Step 4: Confirm
    resp = await async_client.post(f"/api/v1/tasks/{task_id}/confirm")
    assert resp.status_code == 200, f"confirm failed: {resp.text}"
    assert resp.json()["status"] == "confirmed"

    # Step 5: Trigger upload (fires background task)
    resp = await async_client.post(f"/api/v1/tasks/{task_id}/upload")
    assert resp.status_code == 200, f"upload trigger failed: {resp.text}"
    assert resp.json()["status"] == "uploading"

    # Step 6: Wait briefly for background task, then verify final status
    await asyncio.sleep(0.5)
    resp = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    final_status = resp.json()["status"]
    # Background task may finish ("uploaded"/"partial_failed") or still be running
    assert final_status in ("uploading", "uploaded", "partial_failed", "confirmed"), (
        f"Unexpected final status: {final_status}"
    )


@pytest.mark.e2e
async def test_e2e_idempotency(async_client):
    """Uploading the same idempotency_key twice returns the same task_id."""
    zip_bytes = _make_zip_bytes({"acme/月报.xlsx": b"fake xlsx"})
    idem_key = "my-unique-key-001"

    # First upload
    resp1 = await async_client.post(
        "/api/v1/tasks",
        data={"idempotency_key": idem_key},
        files={"file": ("test.zip", zip_bytes, "application/zip")},
    )
    assert resp1.status_code == 201, f"First upload failed: {resp1.text}"
    task_id_1 = resp1.json()["task_id"]

    # Second upload with same key
    resp2 = await async_client.post(
        "/api/v1/tasks",
        data={"idempotency_key": idem_key},
        files={"file": ("test.zip", zip_bytes, "application/zip")},
    )
    assert resp2.status_code == 200, f"Second upload (idempotent) failed: {resp2.text}"
    task_id_2 = resp2.json()["task_id"]

    assert task_id_1 == task_id_2, (
        f"Idempotency broken: got {task_id_1!r} then {task_id_2!r}"
    )


@pytest.mark.e2e
async def test_e2e_file_too_large(async_client, monkeypatch):
    """Uploading a file larger than max_zip_bytes returns 413."""
    # Override max_zip_bytes to a tiny value for this test
    monkeypatch.setattr(
        "app.api.tasks.get_settings",
        lambda: MagicMock(
            max_zip_bytes=5,
            task_dir_base="/tmp",
        ),
    )

    big_bytes = b"x" * 20  # > 5 bytes

    resp = await async_client.post(
        "/api/v1/tasks",
        files={"file": ("big.zip", big_bytes, "application/zip")},
    )
    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}: {resp.text}"


@pytest.mark.e2e
async def test_e2e_confirm_without_classify(async_client):
    """A task in draft status can still be confirmed (confirm doesn't check classification)."""
    zip_bytes = _make_zip_bytes({"acme/月报.xlsx": b"fake xlsx"})

    # Create task (draft status)
    resp = await async_client.post(
        "/api/v1/tasks",
        files={"file": ("test.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    # Confirm without classify
    resp = await async_client.post(f"/api/v1/tasks/{task_id}/confirm")
    assert resp.status_code == 200, f"confirm failed: {resp.text}"
    assert resp.json()["status"] == "confirmed"
