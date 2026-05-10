from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class TaskEvent(Base):
    """ORM model for the `task_event` table.

    event_type allowed values:
        task_created / classified / confirmed / upload_started /
        item_uploaded / item_failed / task_completed
    """

    __tablename__ = "task_event"
    __table_args__ = (Index("ix_task_event_task_created", "task_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=gen_id)
    task_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TaskEvent id={self.id!r} task_id={self.task_id!r} type={self.event_type!r}>"
