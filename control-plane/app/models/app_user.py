from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppUser(Base):
    """ORM model for the `app_user` table."""

    __tablename__ = "app_user"
    __table_args__ = (Index("ix_app_user_tenant_role", "tenant_id", "role"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="subsidiary_viewer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
