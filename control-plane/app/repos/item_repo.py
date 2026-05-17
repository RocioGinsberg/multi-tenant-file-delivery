from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskItem


class ItemRepo:
    """Repository for task item persistence."""

    async def bulk_insert(
        self,
        session: AsyncSession,
        task_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> list[TaskItem]:
        task_items = [TaskItem(task_id=task_id, **dict(item)) for item in items]
        if not task_items:
            return []

        session.add_all(task_items)
        await session.flush()
        for item in task_items:
            await session.refresh(item)
        return task_items

    async def list_by_task(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> list[TaskItem]:
        result = await session.execute(
            select(TaskItem)
            .where(TaskItem.task_id == task_id)
            .order_by(TaskItem.src_path.asc(), TaskItem.id.asc())
        )
        return list(result.scalars().all())

    async def update_upload_status(
        self,
        session: AsyncSession,
        item_id: str,
        upload_status: str,
        *,
        upload_error: str = "",
        uploaded_at: datetime | None = None,
    ) -> TaskItem | None:
        item = await session.get(TaskItem, item_id)
        if item is None:
            return None

        item.upload_status = upload_status
        item.upload_error = upload_error
        item.uploaded_at = uploaded_at
        await session.flush()
        return item

    async def count_by_status(self, session: AsyncSession, task_id: str) -> dict[str, int]:
        result = await session.execute(
            select(TaskItem.upload_status, func.count(TaskItem.id))
            .where(TaskItem.task_id == task_id)
            .group_by(TaskItem.upload_status)
        )
        return {status: count for status, count in result.all()}

    async def batch_reset_failed(self, session: AsyncSession, task_id: str) -> int:
        result = await session.execute(
            update(TaskItem)
            .where(TaskItem.task_id == task_id, TaskItem.upload_status == "failed")
            .values(upload_status="pending", upload_error="", uploaded_at=None)
        )
        await session.flush()
        return result.rowcount or 0
