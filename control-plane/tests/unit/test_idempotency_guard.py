from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.settings import Settings
from app.services import idempotency_guard
from app.services.idempotency_guard import (
    DisabledIdempotencyGuard,
    RedisIdempotencyGuard,
    create_idempotency_guard,
)


@pytest.mark.asyncio
async def test_disabled_guard_always_acquires_and_closes():
    guard = create_idempotency_guard(Settings(redis_idempotency_enabled=False))

    claim = await guard.acquire("create task", "idem key")
    await guard.release(claim)
    await guard.aclose()

    assert isinstance(guard, DisabledIdempotencyGuard)
    assert claim.acquired is True
    assert claim.key == "idempotency:v1:create_task:idem_key"


@pytest.mark.asyncio
async def test_redis_guard_acquire_release_and_close(monkeypatch):
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
    monkeypatch.setattr(idempotency_guard.Redis, "from_url", lambda *args, **kwargs: fake)

    guard = RedisIdempotencyGuard(
        Settings(
            redis_idempotency_enabled=True,
            redis_idempotency_ttl_seconds=30,
        )
    )
    claim = await guard.acquire("upload_task", "task-1")
    await guard.release(claim)
    await guard.aclose()

    assert claim.acquired is True
    assert claim.key == "idempotency:v1:upload_task:task-1"
    assert fake.set_calls == [
        {
            "key": "idempotency:v1:upload_task:task-1",
            "value": claim.token,
            "nx": True,
            "ex": 30,
        }
    ]
    assert fake.eval_calls == [
        (idempotency_guard._RELEASE_SCRIPT, 1, claim.key, claim.token)
    ]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_redis_guard_reports_duplicate_claim(monkeypatch):
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=False)
    fake.aclose = AsyncMock()
    monkeypatch.setattr(idempotency_guard.Redis, "from_url", lambda *args, **kwargs: fake)

    guard = RedisIdempotencyGuard(Settings(redis_idempotency_enabled=True))
    claim = await guard.acquire("create_task", "idem-1")

    assert claim.acquired is False
    fake.set.assert_awaited_once()
