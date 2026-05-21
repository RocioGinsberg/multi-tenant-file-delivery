from __future__ import annotations

import os

import pytest

from app.core.settings import Settings
from app.services.redis_client import create_redis_client


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_ping_docker():
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")

    redis_client = create_redis_client(
        Settings(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            redis_socket_timeout_seconds=1.0,
        )
    )
    try:
        assert await redis_client.ping() is True
    finally:
        await redis_client.close()
