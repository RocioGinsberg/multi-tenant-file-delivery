from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskEvent


class EventRepo:
    """Repository for append-only task events."""

    async def append(
        self,
        session: AsyncSession,
        task_id: str,
        event_type: str,
        payload_json: dict[str, Any] | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            task_id=task_id,
            event_type=event_type,
            payload_json=payload_json or {},
        )
        session.add(event)
        await session.flush()
        await session.refresh(event)
        return event

    async def list_by_task(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        tenant_id: str | None = None,
    ) -> list[TaskEvent]:
        query = select(TaskEvent).where(TaskEvent.task_id == task_id)
        if tenant_id is not None:
            query = query.join(Task, Task.id == TaskEvent.task_id).where(
                Task.owner_tenant_id == tenant_id
            )
        result = await session.execute(
            query.order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
        )
        return list(result.scalars().all())
