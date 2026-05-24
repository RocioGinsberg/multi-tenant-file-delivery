from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class WorkspaceObject(Base):
    """Logical file visible inside a workspace."""

    __tablename__ = "workspace_object"
    __table_args__ = (
        UniqueConstraint("task_item_id", name="uq_workspace_object_task_item"),
        Index("ix_workspace_object_workspace_uploaded", "workspace_id", "uploaded_at"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=gen_id)
    workspace_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    physical_object_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("physical_object.id", ondelete="RESTRICT"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("task.id", ondelete="RESTRICT"),
        nullable=False,
    )
    task_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("task_item.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dst_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("app_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
