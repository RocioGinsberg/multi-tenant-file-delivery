from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.settings import Settings
from app.services import redis_lease
from app.services.redis_lease import (
    DisabledLeaseClient,
    LeaseClaim,
    RedisLeaseClient,
    create_redis_lease,
)


@pytest.mark.asyncio
async def test_disabled_lease_always_acquires_refreshes_and_closes():
    lease = create_redis_lease(Settings(redis_lease_enabled=False))

    claim = await lease.acquire("delivery result")
    refreshed = await lease.refresh(claim)
    await lease.release(claim)
    await lease.aclose()

    assert isinstance(lease, DisabledLeaseClient)
    assert claim.acquired is True
    assert claim.key == "lease:v1:delivery_result"
    assert refreshed is True


@pytest.mark.asyncio
async def test_redis_lease_acquire_refresh_release_and_close(monkeypatch):
    class FakeRedis:
        def __init__(self) -> None:
            self.set_calls: list[dict] = []
            self.eval_calls: list[tuple] = []
            self.closed = False

        async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
            self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
            return True

        async def eval(self, *args) -> int:
            self.eval_calls.append(args)
            return 1

        async def aclose(self) -> None:
            self.closed = True

    fake = FakeRedis()
    monkeypatch.setattr(redis_lease.Redis, "from_url", lambda *args, **kwargs: fake)

    lease = RedisLeaseClient(
        Settings(
            redis_lease_enabled=True,
            redis_lease_ttl_seconds=45,
        )
    )
    claim = await lease.acquire("delivery_result_apply:task-1")
    refreshed = await lease.refresh(claim)
    await lease.release(claim)
    await lease.aclose()

    assert claim.acquired is True
    assert claim.key == "lease:v1:delivery_result_apply:task-1"
    assert refreshed is True
    assert fake.set_calls == [
        {
            "key": "lease:v1:delivery_result_apply:task-1",
            "value": claim.token,
            "nx": True,
            "ex": 45,
        }
    ]
    assert fake.eval_calls == [
        (redis_lease._REFRESH_SCRIPT, 1, claim.key, claim.token, 45),
        (redis_lease._RELEASE_SCRIPT, 1, claim.key, claim.token),
    ]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_redis_lease_reports_already_held_claim(monkeypatch):
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=False)
    fake.aclose = AsyncMock()
    monkeypatch.setattr(redis_lease.Redis, "from_url", lambda *args, **kwargs: fake)

    lease = RedisLeaseClient(Settings(redis_lease_enabled=True))
    claim = await lease.acquire("delivery_result_apply:task-1")

    assert claim.acquired is False
    fake.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_lease_refresh_and_release_ignore_unacquired_claim(monkeypatch):
    fake = AsyncMock()
    fake.eval = AsyncMock()
    fake.aclose = AsyncMock()
    monkeypatch.setattr(redis_lease.Redis, "from_url", lambda *args, **kwargs: fake)

    lease = RedisLeaseClient(Settings(redis_lease_enabled=True))
    claim = LeaseClaim(key="lease:v1:busy", token="loser", acquired=False)

    refreshed = await lease.refresh(claim)
    await lease.release(claim)

    assert refreshed is False
    fake.eval.assert_not_awaited()
