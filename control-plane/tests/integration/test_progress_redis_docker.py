from __future__ import annotations

import asyncio
import inspect
import os

import pytest

from app.core.settings import Settings


async def _close_bus(bus: object) -> None:
    close = getattr(bus, "aclose", None) or getattr(bus, "close", None)
    if close is None:
        return

    result = close()
    if inspect.isawaitable(result):
        await result


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_progress_bus_fans_out_across_instances():
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")

    from app.services.progress_bus import create_progress_bus

    settings = Settings(
        progress_backend="redis",
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_socket_timeout_seconds=1.0,
    )
    subscriber_bus = create_progress_bus(settings)
    publisher_bus = create_progress_bus(settings)
    task_id = "progress-redis-docker-fanout"
    event = {"type": "progress", "pct": 42, "message": "fanout"}

    try:
        async with subscriber_bus.subscribe(task_id) as queue:
            await asyncio.wait_for(publisher_bus.publish(task_id, event), timeout=1.0)

            received = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert received == event

            await asyncio.wait_for(publisher_bus.close_task(task_id), timeout=1.0)
            closed = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert closed is None
    finally:
        await _close_bus(subscriber_bus)
        await _close_bus(publisher_bus)
