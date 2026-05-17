"""Phase 1 TDD — ProgressBus (red phase).

All 11 tests should FAIL with ModuleNotFoundError until progress_bus.py is implemented.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def ProgressBus():  # noqa: N802
    """Deferred import so each test errors individually (not at collection time)."""
    module = importlib.import_module("app.services.progress_bus")
    return module.ProgressBus


# ---------------------------------------------------------------------------
# Cat A — 基础投递（3）
# ---------------------------------------------------------------------------


async def test_publish_to_single_subscriber(ProgressBus):
    """1 个订阅者，publish 1 条 → queue.get() 得到同一个 dict。"""
    bus = ProgressBus()
    event = {"type": "progress", "pct": 50}
    async with bus.subscribe("task-1") as q:
        await bus.publish("task-1", event)
        received = await q.get()
    assert received == event


async def test_publish_multiple_events_ordered(ProgressBus):
    """1 个订阅者，publish 3 条 → 按顺序 get 到，顺序与 publish 一致。"""
    bus = ProgressBus()
    events = [
        {"type": "progress", "pct": 10},
        {"type": "progress", "pct": 50},
        {"type": "done", "pct": 100},
    ]
    async with bus.subscribe("task-2") as q:
        for e in events:
            await bus.publish("task-2", e)
        received = [await q.get() for _ in events]
    assert received == events


async def test_no_subscriber_publish_does_not_block(ProgressBus):
    """无订阅者时 publish 不阻塞，不抛异常。"""
    bus = ProgressBus()
    await asyncio.wait_for(
        bus.publish("task-orphan", {"type": "progress", "pct": 1}),
        timeout=1.0,
    )


# ---------------------------------------------------------------------------
# Cat B — Fanout（2）
# ---------------------------------------------------------------------------


async def test_fanout_to_multiple_subscribers(ProgressBus):
    """2 个订阅者订阅同一 task_id → publish 1 条，两个 queue 各自收到该条消息。"""
    bus = ProgressBus()
    event = {"type": "progress", "pct": 75}
    async with bus.subscribe("task-fan") as q1:
        async with bus.subscribe("task-fan") as q2:
            await bus.publish("task-fan", event)
            r1 = await q1.get()
            r2 = await q2.get()
    assert r1 == event
    assert r2 == event


async def test_different_task_ids_isolated(ProgressBus):
    """订阅 task-A，publish 到 task-B → queue 为空，wait_for 超时。"""
    bus = ProgressBus()
    async with bus.subscribe("task-A") as q:
        await bus.publish("task-B", {"type": "progress", "pct": 20})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.1)


# ---------------------------------------------------------------------------
# Cat C — 订阅者生命周期（3）
# ---------------------------------------------------------------------------


async def test_subscriber_exits_cleanly(ProgressBus):
    """async with 块结束後 publish 不抛异常（注册表已清理）。"""
    bus = ProgressBus()
    async with bus.subscribe("task-exit"):
        pass
    # After exiting context, publish should succeed without error
    await bus.publish("task-exit", {"type": "done"})


async def test_subscriber_cancel_does_not_affect_others(ProgressBus):
    """cancel 一个等待中的订阅者任务，另一个订阅者仍可正常收到消息。"""
    bus = ProgressBus()
    event = {"type": "progress", "pct": 33}
    async with bus.subscribe("task-cancel") as q1:
        async with bus.subscribe("task-cancel") as q2:
            # Start a task that waits forever on q1
            waiting_task = asyncio.create_task(q1.get())
            # Allow the task to start
            await asyncio.sleep(0)
            # Cancel it
            waiting_task.cancel()
            try:
                await waiting_task
            except asyncio.CancelledError:
                pass
            # Publish after cancellation — q2 should still receive
            await bus.publish("task-cancel", event)
            received = await q2.get()
    assert received == event


async def test_resubscribe_after_exit(ProgressBus):
    """退出 context 后再次订阅同一 task_id，publish 1 条 → 新 queue 收到消息。"""
    bus = ProgressBus()
    event = {"type": "progress", "pct": 99}
    # First subscription — enter and exit
    async with bus.subscribe("task-resub"):
        pass
    # Second subscription — should work independently
    async with bus.subscribe("task-resub") as q:
        await bus.publish("task-resub", event)
        received = await q.get()
    assert received == event


# ---------------------------------------------------------------------------
# Cat D — close_task（2）
# ---------------------------------------------------------------------------


async def test_close_task_sends_sentinel(ProgressBus):
    """close_task 后 queue.get() 得到 None（sentinel）。"""
    bus = ProgressBus()
    async with bus.subscribe("task-close") as q:
        await bus.close_task("task-close")
        sentinel = await q.get()
    assert sentinel is None


async def test_close_task_no_subscribers_does_not_raise(ProgressBus):
    """无订阅者时调用 close_task 不抛异常。"""
    bus = ProgressBus()
    await bus.close_task("task-nobody")


# ---------------------------------------------------------------------------
# Cat E — 慢消费者（1）
# ---------------------------------------------------------------------------


async def test_slow_consumer_does_not_block_publisher(ProgressBus):
    """订阅者不消费；publish 10 条仍立即全部完成（queue maxsize=0 无限）。"""
    bus = ProgressBus()
    events = [{"type": "progress", "pct": i * 10} for i in range(10)]
    async with bus.subscribe("task-slow") as q:
        publishes = [bus.publish("task-slow", e) for e in events]
        await asyncio.wait_for(asyncio.gather(*publishes), timeout=1.0)
        # Now consume all 10 events in order
        received = [await q.get() for _ in events]
    assert received == events
