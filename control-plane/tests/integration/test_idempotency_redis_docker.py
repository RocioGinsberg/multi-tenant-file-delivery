from __future__ import annotations

import inspect
import os
import uuid

import pytest

from app.core.settings import Settings


async def _maybe_await(result: object) -> object:
    if inspect.isawaitable(result):
        return await result
    return result


async def _close_guard(guard: object) -> None:
    close = getattr(guard, "aclose", None) or getattr(guard, "close", None)
    if close is None:
        return

    await _maybe_await(close())


def _is_acquired(claim: object) -> bool:
    if isinstance(claim, bool):
        return claim
    if not hasattr(claim, "acquired"):
        return False
    return bool(claim.acquired)


async def _release_claim(
    guard: object,
    operation: str,
    identity: str,
    claim: object,
) -> None:
    if not _is_acquired(claim):
        return

    release = getattr(guard, "release", None)
    if release is None:
        return

    try:
        await _maybe_await(release(claim))
    except TypeError:
        await _maybe_await(release(operation, identity))


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_idempotency_guard_claims_across_instances():
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")

    from app.services.idempotency_guard import create_idempotency_guard

    settings = Settings(
        redis_idempotency_enabled=True,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_socket_timeout_seconds=1.0,
        redis_idempotency_ttl_seconds=30,
    )
    guard1 = create_idempotency_guard(settings)
    guard2 = create_idempotency_guard(settings)
    operation = "phase4.4.redis-idempotency-docker"
    identity = f"identity-{uuid.uuid4().hex}"
    claim1 = None
    claim2_after_release = None

    try:
        claim1 = await _maybe_await(guard1.acquire(operation, identity))
        assert _is_acquired(claim1) is True

        duplicate_claim = await _maybe_await(guard2.acquire(operation, identity))
        assert _is_acquired(duplicate_claim) is False

        await _release_claim(guard1, operation, identity, claim1)
        claim1 = None

        claim2_after_release = await _maybe_await(guard2.acquire(operation, identity))
        assert _is_acquired(claim2_after_release) is True
    finally:
        if claim1 is not None:
            await _release_claim(guard1, operation, identity, claim1)
        if claim2_after_release is not None:
            await _release_claim(guard2, operation, identity, claim2_after_release)
        await _close_guard(guard1)
        await _close_guard(guard2)
