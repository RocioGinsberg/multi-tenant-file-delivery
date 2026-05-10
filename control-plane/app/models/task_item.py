from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class TaskItem(Base):
    """ORM model for the `task_item` table.

    severity:      ok / warning / error / ignored
    upload_status: pending / uploading / uploaded / failed / skipped
    """

    __tablename__ = "task_item"
    __table_args__ = (
        UniqueConstraint("task_id", "src_path", name="uq_task_item_task_src"),
        Index("ix_task_item_task_id", "task_id"),
        Index("ix_task_item_task_upload", "task_id", "upload_status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=gen_id)
    task_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )
    src_path: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Extension including leading dot, e.g. ".xlsx"
    ext: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    team_name_raw: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    team_name_matched: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    task_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    category_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    drive_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    drive_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    warning_message: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    upload_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    upload_error: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<TaskItem id={self.id!r} task_id={self.task_id!r} src_path={self.src_path!r}>"
