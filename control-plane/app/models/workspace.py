from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class Workspace(Base):
    """Logical read container owned by HQ and targeted to one subsidiary tenant."""

    __tablename__ = "workspace"
    __table_args__ = (
        UniqueConstraint(
            "owner_tenant_id",
            "target_key",
            name="uq_workspace_owner_target_key",
        ),
        Index("ix_workspace_owner_tenant", "owner_tenant_id"),
        Index("ix_workspace_target_tenant", "target_tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
