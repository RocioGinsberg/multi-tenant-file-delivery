from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task


class TaskRepo:
    """Repository for task persistence.

    Transaction ownership stays with the caller; repository write methods flush
    but never commit.
    """

    async def create(
        self,
        session: AsyncSession,
        *,
        idempotency_key: str,
        submission_label: str = "",
        temp_dir: str = "",
        summary_json: dict[str, Any] | None = None,
        created_by: str = "local-user",
        status: str = "draft",
    ) -> Task:
        task = Task(
            idempotency_key=idempotency_key,
            submission_label=submission_label,
            temp_dir=temp_dir,
            summary_json=summary_json or {},
            created_by=created_by,
            status=status,
        )
        session.add(task)
        await session.flush()
        await session.refresh(task)
        return task

    async def get(self, session: AsyncSession, task_id: str) -> Task | None:
        return await session.get(Task, task_id)

    async def get_by_idempotency_key(
        self,
        session: AsyncSession,
        idempotency_key: str,
    ) -> Task | None:
        result = await session.execute(
            select(Task).where(Task.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        session: AsyncSession,
        task_id: str,
        status: str,
        *,
        confirmed_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> Task | None:
        task = await session.get(Task, task_id)
        if task is None:
            return None

        task.status = status
        if confirmed_at is not None:
            task.confirmed_at = confirmed_at
        if finished_at is not None:
            task.finished_at = finished_at

        await session.flush()
        return task

    async def list(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        result = await session.execute(
            select(Task)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
