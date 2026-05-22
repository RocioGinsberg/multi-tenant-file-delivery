from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tenant(Base):
    """ORM model for the `tenant` table."""

    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tenant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="subsidiary")
    parent_tenant_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
