from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import Settings
from app.models import Base, Task
from app.repos.item_repo import ItemRepo
from app.services.delivery import (
    FileSpoolDeliveryPublisher,
    FileSpoolDeliveryResultConsumer,
    KafkaDeliveryPublisher,
    KafkaDeliveryResultConsumer,
    build_delivery_task_message,
    consume_delivery_results,
)
from app.services.progress_bus import create_progress_bus
from app.services.staging_source import stage_task_archive, task_archive_path

GO_RUN_TIMEOUT_SECONDS = 120


def _go_test_env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GOTOOLCHAIN": "auto",
        "GOPATH": "/tmp/smh_go_path",
        "GOMODCACHE": "/tmp/smh_go_mod_cache",
        "GOCACHE": "/tmp/smh_go_cache",
    })
    env.update(extra)
    return env


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
        env=_go_test_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=GO_RUN_TIMEOUT_SECONDS,
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
        env=_go_test_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=GO_RUN_TIMEOUT_SECONDS,
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


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_reference_kafka_bridge_round_trip(
    session: AsyncSession,
    tmp_path: Path,
):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml kafka/minio running")
    if shutil.which("go") is None:
        pytest.skip("go command is required for source reference Kafka bridge test")

    from app.core.settings import get_settings

    repo_root = Path(__file__).resolve().parents[3]
    data_plane_dir = repo_root / "data-plane"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    with zipfile.ZipFile(task_dir / "original.zip", "w") as zf:
        zf.writestr("report.txt", "hello")

    suffix = uuid.uuid4().hex
    task_topic = f"delivery.tasks.source.pytest.{suffix}"
    result_topic = f"delivery.results.source.pytest.{suffix}"
    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    await _create_kafka_topics(brokers, task_topic, result_topic)

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
        idempotency_key=f"idem-source-kafka-{suffix}",
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
        await KafkaDeliveryPublisher(
            bootstrap_servers=brokers,
            topic=task_topic,
        ).publish(message)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    run_result = subprocess.run(
        [
            "go",
            "run",
            "./cmd/worker",
            "-transport",
            "kafka",
            "-kafka-brokers",
            brokers,
            "-kafka-task-topic",
            task_topic,
            "-kafka-result-topic",
            result_topic,
            "-kafka-group-id",
            f"data-plane-source-test-{suffix}",
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
        env=_go_test_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=GO_RUN_TIMEOUT_SECONDS,
    )
    assert run_result.returncode == 0, run_result.stderr

    summaries = await consume_delivery_results(
        session,
        KafkaDeliveryResultConsumer(
            bootstrap_servers=brokers,
            topic=result_topic,
            group_id=f"control-plane-source-result-test-{suffix}",
            timeout_ms=10_000,
        ),
    )
    await session.commit()

    updated_items = await item_repo.list_by_task(session, task.id)
    assert len(summaries) == 1
    assert summaries[0].status == "uploaded"
    assert summaries[0].applied_items == 1
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_source_reference_kafka_task_keeps_final_state_stable(
    session: AsyncSession,
    tmp_path: Path,
):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml kafka/minio running")
    if shutil.which("go") is None:
        pytest.skip("go command is required for source reference Kafka duplicate test")

    from app.core.settings import get_settings

    repo_root = Path(__file__).resolve().parents[3]
    data_plane_dir = repo_root / "data-plane"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    with zipfile.ZipFile(task_dir / "original.zip", "w") as zf:
        zf.writestr("report.txt", "hello")

    suffix = uuid.uuid4().hex
    task_topic = f"delivery.tasks.duplicate.pytest.{suffix}"
    result_topic = f"delivery.results.duplicate.pytest.{suffix}"
    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    await _create_kafka_topics(brokers, task_topic, result_topic)

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
        idempotency_key=f"idem-source-duplicate-{suffix}",
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
        publisher = KafkaDeliveryPublisher(
            bootstrap_servers=brokers,
            topic=task_topic,
        )
        await publisher.publish(message)
        await publisher.publish(message)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    run_result = subprocess.run(
        [
            "go",
            "run",
            "./cmd/worker",
            "-transport",
            "kafka",
            "-kafka-brokers",
            brokers,
            "-kafka-task-topic",
            task_topic,
            "-kafka-result-topic",
            result_topic,
            "-kafka-group-id",
            f"data-plane-duplicate-test-{suffix}",
            "-kafka-batch-size",
            "2",
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
        env=_go_test_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=GO_RUN_TIMEOUT_SECONDS,
    )
    assert run_result.returncode == 0, run_result.stderr

    summaries = await consume_delivery_results(
        session,
        KafkaDeliveryResultConsumer(
            bootstrap_servers=brokers,
            topic=result_topic,
            group_id=f"control-plane-duplicate-result-test-{suffix}",
            timeout_ms=10_000,
        ),
    )
    await session.commit()

    updated_items = await item_repo.list_by_task(session, task.id)
    assert len(summaries) == 2
    assert [summary.status for summary in summaries] == ["uploaded", "uploaded"]
    assert [summary.applied_items for summary in summaries] == [1, 1]
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"
    assert updated_items[0].uploaded_at is not None


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase4_redis_kafka_object_source_smoke(
    session: AsyncSession,
    tmp_path: Path,
):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip(
            "set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml kafka/minio/redis running"
        )
    if shutil.which("go") is None:
        pytest.skip("go command is required for Phase 4 Redis smoke")

    from app.core.settings import get_settings

    repo_root = Path(__file__).resolve().parents[3]
    data_plane_dir = repo_root / "data-plane"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    internal_archive = Path(task_archive_path(str(task_dir)))
    internal_archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(internal_archive, "w") as zf:
        zf.writestr("acme/report.txt", "hello")

    suffix = uuid.uuid4().hex
    task_topic = f"delivery.tasks.phase4.pytest.{suffix}"
    result_topic = f"delivery.results.phase4.pytest.{suffix}"
    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    await _create_kafka_topics(brokers, task_topic, result_topic)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    progress_settings = Settings(
        progress_backend="redis",
        redis_url=redis_url,
        redis_socket_timeout_seconds=1.0,
    )
    subscriber_bus = create_progress_bus(progress_settings)
    publisher_bus = create_progress_bus(progress_settings)
    try:
        async with subscriber_bus.subscribe(f"phase4-progress-{suffix}") as progress_queue:
            progress_event = {"type": "phase4_smoke", "suffix": suffix}
            await publisher_bus.publish(f"phase4-progress-{suffix}", progress_event)
            assert await asyncio.wait_for(progress_queue.get(), timeout=2.0) == progress_event
    finally:
        await subscriber_bus.aclose()
        await publisher_bus.aclose()

    env_overrides = {
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_ACCESS_KEY_ID": "minioadmin",
        "S3_SECRET_ACCESS_KEY": "minioadmin",
        "S3_REGION": "us-east-1",
        "STAGING_BUCKET_NAME": "auto-upload-staging",
        "REDIS_LEASE_ENABLED": "true",
        "REDIS_URL": redis_url,
        "REDIS_SOCKET_TIMEOUT_SECONDS": "1.0",
        "REDIS_LEASE_TTL_SECONDS": "5",
    }
    old_env = {key: os.environ.get(key) for key in env_overrides}
    os.environ.update(env_overrides)
    get_settings.cache_clear()

    item_repo = ItemRepo()
    task = Task(
        idempotency_key=f"idem-phase4-{suffix}",
        submission_label="folder-upload",
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
                "src_path": "acme/report.txt",
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
        await KafkaDeliveryPublisher(
            bootstrap_servers=brokers,
            topic=task_topic,
        ).publish(message)

        run_result = subprocess.run(
            [
                "go",
                "run",
                "./cmd/worker",
                "-transport",
                "kafka",
                "-kafka-brokers",
                brokers,
                "-kafka-task-topic",
                task_topic,
                "-kafka-result-topic",
                result_topic,
                "-kafka-group-id",
                f"data-plane-phase4-test-{suffix}",
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
                "-redis-url",
                redis_url,
                "-redis-limiter-enabled",
                "-redis-limiter-key",
                "phase4-smoke",
                "-redis-limiter-limit",
                "10",
                "-redis-limiter-window",
                "1s",
            ],
            cwd=data_plane_dir,
            env=_go_test_env(REDIS_URL=redis_url),
            text=True,
            capture_output=True,
            check=False,
            timeout=GO_RUN_TIMEOUT_SECONDS,
        )
        assert run_result.returncode == 0, run_result.stderr

        summaries = await consume_delivery_results(
            session,
            KafkaDeliveryResultConsumer(
                bootstrap_servers=brokers,
                topic=result_topic,
                group_id=f"control-plane-phase4-result-test-{suffix}",
                timeout_ms=10_000,
            ),
        )
        await session.commit()
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    updated_items = await item_repo.list_by_task(session, task.id)
    assert len(summaries) == 1
    assert summaries[0].status == "uploaded"
    assert summaries[0].applied_items == 1
    assert task.status == "uploaded"
    assert updated_items[0].upload_status == "uploaded"


async def _create_kafka_topics(bootstrap_servers: str, *topics: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    await admin.start()
    try:
        await admin.create_topics([
            NewTopic(name=topic, num_partitions=1, replication_factor=1)
            for topic in topics
        ])
    except TopicAlreadyExistsError:
        return
    finally:
        await admin.close()
