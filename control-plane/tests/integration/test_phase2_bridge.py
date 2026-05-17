from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task
from app.repos.item_repo import ItemRepo
from app.services.delivery import (
    FileSpoolDeliveryPublisher,
    FileSpoolDeliveryResultConsumer,
    build_delivery_task_message,
    consume_delivery_results,
)
from app.services.staging_source import stage_task_archive


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        yield db_session

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase2_file_spool_bridge_round_trip(
    session: AsyncSession,
    tmp_path: Path,
):
    if shutil.which("go") is None:
        pytest.skip("go command is required for phase2 bridge integration test")

    repo_root = Path(__file__).resolve().parents[3]
    data_plane_dir = repo_root / "data-plane"
    outbox_dir = tmp_path / "outbox"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "report.txt").write_text("hello", encoding="utf-8")

    item_repo = ItemRepo()
    task = Task(
        idempotency_key="idem-phase2-bridge",
        submission_label="upload.zip",
        temp_dir=str(task_dir),
        status="confirmed",
    )
    session.add(task)
    await session.flush()
    items = await item_repo.bulk_insert(
        session,
        task.id,
        [
            {
                "src_path": "report.txt",
                "filename": "report.txt",
                "ext": ".txt",
                "file_size": 5,
                "target_name_raw": "acme",
                "target_name_matched": "acme",
                "document_type": "report",
                "category_name": "reports",
                "dst_dir": "reports",
                "dst_path": "reports/report.txt",
                "severity": "ok",
                "upload_status": "pending",
            },
        ],
    )

    publisher = FileSpoolDeliveryPublisher(outbox_dir)
    message = build_delivery_task_message(
        task=task,
        upload_items=items,
        bucket_name="auto-upload-dev",
    )
    task_message_path = await publisher.publish(message)
    assert task_message_path.exists()

    env = os.environ.copy()
    env.update({
        "GOPATH": "/tmp/smh_go_path",
        "GOMODCACHE": "/tmp/smh_go_path/pkg/mod",
        "GOCACHE": "/tmp/smh_go_cache",
    })
    run_result = subprocess.run(
        [
            "go",
            "run",
            "./cmd/worker",
            "-inbox",
            str(outbox_dir / "delivery.tasks.v1"),
            "-results",
            str(outbox_dir / "delivery.results.v1"),
            "-sink",
            "mock",
        ],
        cwd=data_plane_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run_result.returncode == 0, run_result.stderr

    summaries = await consume_delivery_results(
        session,
        FileSpoolDeliveryResultConsumer(outbox_dir),
    )
    await session.commit()

    updated_items = await item_repo.list_by_task(session, task.id)
    assert len(summaries) == 1
    assert summaries[0].status == "uploaded"
    assert summaries[0].applied_items == 1
    assert task.status == "uploaded"
    assert task.finished_at is not None
    assert updated_items[0].upload_status == "uploaded"
    assert updated_items[0].uploaded_at is not None


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_reference_file_spool_bridge_round_trip(
    session: AsyncSession,
    tmp_path: Path,
):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml minio running")
    if shutil.which("go") is None:
        pytest.skip("go command is required for source reference bridge integration test")

    from app.core.settings import get_settings

    repo_root = Path(__file__).resolve().parents[3]
    data_plane_dir = repo_root / "data-plane"
    outbox_dir = tmp_path / "outbox"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    original_zip = task_dir / "original.zip"
    with zipfile.ZipFile(original_zip, "w") as zf:
        zf.writestr("report.txt", "hello")

    env_overrides = {
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_ACCESS_KEY_ID": "minioadmin",
        "S3_SECRET_ACCESS_KEY": "minioadmin",
        "S3_REGION": "us-east-1",
        "STAGING_BUCKET_NAME": "auto-upload-staging",
    }
    old_env = {key: os.environ.get(key) for key in env_overrides}
    os.environ.update(env_overrides)
    get_settings.cache_clear()

    item_repo = ItemRepo()
    task = Task(
        idempotency_key="idem-source-bridge",
        submission_label="upload.zip",
        temp_dir=str(task_dir),
        status="confirmed",
    )
    session.add(task)
    await session.flush()
    items = await item_repo.bulk_insert(
        session,
        task.id,
        [
            {
                "src_path": "report.txt",
                "filename": "report.txt",
                "ext": ".txt",
                "file_size": 5,
                "target_name_raw": "acme",
                "target_name_matched": "acme",
                "document_type": "report",
                "category_name": "reports",
                "dst_dir": "reports",
                "dst_path": "reports/report.txt",
                "severity": "ok",
                "upload_status": "pending",
            },
        ],
    )

    try:
        source_ref = await stage_task_archive(task)
        message = build_delivery_task_message(
            task=task,
            upload_items=items,
            bucket_name="auto-upload-dev",
            source=source_ref,
        )
        task_message_path = await FileSpoolDeliveryPublisher(outbox_dir).publish(message)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    assert task_message_path.exists()

    env = os.environ.copy()
    env.update({
        "GOPATH": "/tmp/smh_go_path",
        "GOMODCACHE": "/tmp/smh_go_path/pkg/mod",
        "GOCACHE": "/tmp/smh_go_cache",
    })
    run_result = subprocess.run(
        [
            "go",
            "run",
            "./cmd/worker",
            "-inbox",
            str(outbox_dir / "delivery.tasks.v1"),
            "-results",
            str(outbox_dir / "delivery.results.v1"),
            "-sink",
            "mock",
            "-source-mode",
            "object",
            "-s3-endpoint",
            "http://localhost:9000",
            "-s3-region",
            "us-east-1",
            "-s3-access-key-id",
            "minioadmin",
            "-s3-secret-access-key",
            "minioadmin",
            "-s3-path-style=true",
        ],
        cwd=data_plane_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert run_result.returncode == 0, run_result.stderr

    summaries = await consume_delivery_results(
        session,
        FileSpoolDeliveryResultConsumer(outbox_dir),
    )
    await session.commit()

    updated_items = await item_repo.list_by_task(session, task.id)
    assert len(summaries) == 1
    assert summaries[0].status == "uploaded"
    assert summaries[0].applied_items == 1
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"
