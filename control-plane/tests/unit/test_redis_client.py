from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.settings import Settings
from app.services import redis_client
from app.services.redis_client import RedisClient, create_redis_client


class FakeRedis:
    def __init__(self, *, ping_result: bool = True) -> None:
        self.ping_result = ping_result
        self.closed = False
        self.pings = 0

    async def ping(self) -> bool:
        self.pings += 1
        return self.ping_result

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_client_ping_and_close():
    fake = FakeRedis()
    client = RedisClient(client=fake)

    assert await client.ping() is True
    await client.close()

    assert fake.pings == 1
    assert fake.closed is True


def test_create_redis_client_uses_settings(monkeypatch):
    redis_instance = MagicMock()
    from_url = MagicMock(return_value=redis_instance)
    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)
    settings = Settings(
        redis_url="redis://redis.example.com:6379/2",
        redis_socket_timeout_seconds=2.5,
    )

    client = create_redis_client(settings)

    assert client.client is redis_instance
    from_url.assert_called_once_with(
        "redis://redis.example.com:6379/2",
        socket_timeout=2.5,
        socket_connect_timeout=2.5,
        decode_responses=True,
    )
