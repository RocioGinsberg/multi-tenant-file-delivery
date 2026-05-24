from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import Settings, get_settings
from app.main import app
from app.models import Base, Task
from app.repos.item_repo import ItemRepo
from app.services import tracing as control_tracing
from app.services.delivery import (
    KafkaDeliveryPublisher,
    KafkaDeliveryResultConsumer,
    build_delivery_task_message,
    consume_delivery_results,
)
from app.services.staging_source import stage_task_archive, task_archive_path
from tests.integration.test_phase2_bridge import _create_kafka_topics

CONTROL_TRACE_SPANS = (
    "phase5.observability.publish",
    "control_plane.delivery.task_publish",
)
DATA_TRACE_SPANS = (
    "data_plane.task.process",
    "data_plane.source.resolve",
    "data_plane.sink.upload",
    "data_plane.result.publish",
)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        yield db_session

    await engine.dispose()


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase5_observability_smoke(
    session: AsyncSession,
    tmp_path: Path,
):
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy compose stack running")
    if shutil.which("go") is None:
        pytest.skip("go command is required for Phase 5 observability smoke")
    if shutil.which("docker") is None:
        pytest.skip("docker command is required to inspect collector logs")

    repo_root = Path(__file__).resolve().parents[3]
    _ensure_observability_compose(repo_root / "deploy")
    await _assert_observability_services_ready()
    await _assert_control_plane_metrics_endpoint()

    data_plane_dir = repo_root / "data-plane"
    suffix = uuid.uuid4().hex
    task_topic = f"delivery.tasks.observability.pytest.{suffix}"
    result_topic = f"delivery.results.observability.pytest.{suffix}"
    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    await _create_kafka_topics_with_retry(brokers, task_topic, result_topic)

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    internal_archive = Path(task_archive_path(str(task_dir)))
    internal_archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(internal_archive, "w") as zf:
        zf.writestr("acme/report.txt", "hello")

    env_overrides = {
        "OBSERVABILITY_ENABLED": "true",
        "SERVICE_NAME": f"control-plane-smoke-{suffix}",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        "S3_ENDPOINT_URL": "http://localhost:9000",
        "S3_ACCESS_KEY_ID": "minioadmin",
        "S3_SECRET_ACCESS_KEY": "minioadmin",
        "S3_REGION": "us-east-1",
        "STAGING_BUCKET_NAME": "auto-upload-staging",
        "REDIS_LEASE_ENABLED": "true",
        "REDIS_URL": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        "REDIS_SOCKET_TIMEOUT_SECONDS": "1.0",
        "REDIS_LEASE_TTL_SECONDS": "5",
    }
    old_env = {key: os.environ.get(key) for key in env_overrides}
    os.environ.update(env_overrides)
    get_settings.cache_clear()

    item_repo = ItemRepo()
    task = Task(
        idempotency_key=f"idem-observability-{suffix}",
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

    worker = None
    try:
        metrics_addr, worker = await _start_data_plane_worker(
            data_plane_dir=data_plane_dir,
            brokers=brokers,
            task_topic=task_topic,
            result_topic=result_topic,
            group_id=f"data-plane-observability-test-{suffix}",
            service_name=f"data-plane-smoke-{suffix}",
            redis_url=env_overrides["REDIS_URL"],
        )

        control_tracing.configure_tracing(get_settings())
        with control_tracing.start_trace_span("phase5.observability.publish"):
            source_ref = await stage_task_archive(task)
            message = build_delivery_task_message(
                task=task,
                upload_items=items,
                bucket_name="auto-upload-dev",
                source=source_ref,
            )
            assert message.traceparent is not None
            trace_id = message.traceparent.split("-")[1]
            await KafkaDeliveryPublisher(
                bootstrap_servers=brokers,
                topic=task_topic,
            ).publish(message)
        control_tracing.shutdown_tracing()

        summaries = await consume_delivery_results(
            session,
            KafkaDeliveryResultConsumer(
                bootstrap_servers=brokers,
                topic=result_topic,
                group_id=f"control-plane-observability-result-test-{suffix}",
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

        data_plane_metrics = await _wait_for_metrics(
            f"http://{metrics_addr}/metrics",
            "data_plane_sink_upload_total",
        )
        assert "data_plane_task_consume_total" in data_plane_metrics
        assert "data_plane_source_read_total" in data_plane_metrics
        assert "data_plane_result_publish_total" in data_plane_metrics
        assert 'transport="kafka"' in data_plane_metrics
        assert 'sink="mock"' in data_plane_metrics

        await _wait_for_collector_trace(
            repo_root / "deploy",
            trace_id,
            f"control-plane-smoke-{suffix}",
            f"data-plane-smoke-{suffix}",
        )
    finally:
        control_tracing.shutdown_tracing()
        await _terminate_worker(worker)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


async def _assert_observability_services_ready() -> None:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        prometheus = await client.get("http://localhost:9090/-/ready")
        assert prometheus.status_code == 200
        collector = await client.get("http://localhost:9464/metrics")
        assert collector.status_code == 200
        minio = await client.get("http://localhost:9000/minio/health/live")
        assert minio.status_code == 200


async def _assert_control_plane_metrics_endpoint() -> None:
    settings = Settings(metrics_enabled=True)
    with patch("app.services.metrics.get_settings", return_value=settings):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            trust_env=False,
        ) as client:
            health_resp = await client.get("/healthz")
            metrics_resp = await client.get("/metrics")

    assert health_resp.status_code == 200
    assert metrics_resp.status_code == 200
    body = metrics_resp.text
    assert "control_plane_http_requests_total" in body
    assert 'route="/healthz"' in body


def _ensure_observability_compose(deploy_dir: Path) -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "mysql",
            "kafka",
            "minio",
            "minio-init",
            "redis",
        ],
        cwd=deploy_dir,
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "otel-collector"],
        cwd=deploy_dir,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "prometheus", "grafana"],
        cwd=deploy_dir,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


async def _create_kafka_topics_with_retry(
    brokers: str,
    task_topic: str,
    result_topic: str,
) -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            await _create_kafka_topics(brokers, task_topic, result_topic)
            return
        except Exception as exc:  # pragma: no cover - only exercised while Docker boots.
            last_error = exc
            await asyncio.sleep(1.0)
    raise AssertionError("Kafka topics were not created before timeout") from last_error


async def _start_data_plane_worker(
    *,
    data_plane_dir: Path,
    brokers: str,
    task_topic: str,
    result_topic: str,
    group_id: str,
    service_name: str,
    redis_url: str,
) -> tuple[str, asyncio.subprocess.Process]:
    env = os.environ.copy()
    env.update({
        "GOTOOLCHAIN": "auto",
        "GOPATH": "/tmp/smh_go_path",
        "GOMODCACHE": "/tmp/smh_go_mod_cache",
        "GOCACHE": "/tmp/smh_go_cache",
        "REDIS_URL": redis_url,
    })
    worker = await asyncio.create_subprocess_exec(
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
        group_id,
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
        "phase5-smoke",
        "-redis-limiter-limit",
        "10",
        "-redis-limiter-window",
        "1s",
        "-metrics-enabled",
        "-metrics-listen-addr",
        "127.0.0.1:0",
        "-tracing-enabled",
        "-tracing-service-name",
        service_name,
        "-tracing-otlp-endpoint",
        "http://localhost:4318",
        "-once=false",
        cwd=data_plane_dir,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    metrics_addr = await _read_worker_metrics_addr(worker)
    return metrics_addr, worker


async def _read_worker_metrics_addr(worker: asyncio.subprocess.Process) -> str:
    assert worker.stderr is not None
    prefix = "metrics endpoint listening on "
    stderr_lines: list[str] = []
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        line_bytes = await asyncio.wait_for(worker.stderr.readline(), timeout=5)
        if not line_bytes:
            raise AssertionError(
                f"worker exited before metrics endpoint was logged: {stderr_lines}"
            )
        line = line_bytes.decode("utf-8", errors="replace").strip()
        stderr_lines.append(line)
        if prefix in line:
            return line.split(prefix, 1)[1].strip()
    raise AssertionError(f"metrics endpoint was not logged: {stderr_lines}")


async def _wait_for_metrics(url: str, expected: str) -> str:
    async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
        for _ in range(20):
            response = await client.get(url)
            if response.status_code == 200 and expected in response.text:
                return response.text
            await asyncio.sleep(0.5)
    raise AssertionError(f"{expected!r} was not observed at {url}")


async def _wait_for_collector_trace(
    deploy_dir: Path,
    trace_id: str,
    control_service: str,
    data_service: str,
) -> None:
    last_output = ""
    for _ in range(30):
        logs = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--since", "5m", "otel-collector"],
            cwd=deploy_dir,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        last_output = logs.stdout + logs.stderr
        blocks = _collector_resource_blocks(last_output)
        control_seen = _collector_has_trace_span(
            blocks,
            trace_id,
            control_service,
            CONTROL_TRACE_SPANS,
        )
        data_seen = _collector_has_trace_span(
            blocks,
            trace_id,
            data_service,
            DATA_TRACE_SPANS,
        )
        if control_seen and data_seen:
            return
        await asyncio.sleep(1.0)
    raise AssertionError(
        "collector logs did not include expected same-trace span markers "
        f"trace_id={trace_id} control={control_service} data={data_service} "
        f"observed={_collector_observed_markers(last_output, trace_id)}"
    )


def _collector_resource_blocks(output: str) -> list[str]:
    return [
        f"ResourceSpans #{part}"
        for part in output.split("ResourceSpans #")
        if part.strip()
    ]


def _collector_has_trace_span(
    blocks: list[str],
    trace_id: str,
    service_name: str,
    span_names: tuple[str, ...],
) -> bool:
    return any(
        trace_id in block
        and service_name in block
        and any(span_name in block for span_name in span_names)
        for block in blocks
    )


def _collector_observed_markers(output: str, trace_id: str) -> dict[str, bool]:
    markers = {
        "trace_id": trace_id in output,
        "control_publish": any(name in output for name in CONTROL_TRACE_SPANS),
        "data_process": "data_plane.task.process" in output,
        "data_upload": "data_plane.sink.upload" in output,
        "data_result_publish": "data_plane.result.publish" in output,
    }
    return {key: bool(value) for key, value in markers.items()}


async def _terminate_worker(worker: asyncio.subprocess.Process | None) -> None:
    if worker is None or worker.returncode is not None:
        return
    try:
        os.killpg(worker.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(worker.communicate(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await worker.communicate()
