from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class Task(Base):
    """ORM model for the `task` table.

    status allowed values:
        draft / classifying / classified / classification_failed /
        confirmed / queued / uploading / partial_failed / uploaded / failed
    """

    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint(
            "owner_tenant_id",
            "idempotency_key",
            name="uq_task_owner_tenant_idempotency",
        ),
        Index("ix_task_owner_tenant_id", "owner_tenant_id"),
        Index("ix_task_owner_tenant_user", "owner_tenant_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=gen_id)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="RESTRICT"),
        nullable=False,
        default="hq",
        server_default="hq",
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("app_user.id", ondelete="RESTRICT"),
        nullable=False,
        default="local-user",
        server_default="local-user",
    )
    # User-facing label (e.g. folder label); does not drive business logic.
    submission_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Absolute path to the extracted zip directory.
    temp_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # classify result: {total, ok, warning, error, ignored, has_blocking_errors}
    summary_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Task id={self.id!r} status={self.status!r}>"
