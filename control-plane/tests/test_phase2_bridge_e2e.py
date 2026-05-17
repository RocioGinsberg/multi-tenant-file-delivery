from __future__ import annotations

import os
import shutil
import subprocess
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

    repo_root = Path(__file__).resolve().parents[2]
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
