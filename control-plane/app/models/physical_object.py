from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import gen_id
from app.models.base import Base


class PhysicalObject(Base):
    """Physical sink object metadata.

    Phase 6.5 records metadata only; platform-level dedup hits and reference
    counting stay out of scope until Phase 7.
    """

    __tablename__ = "physical_object"
    __table_args__ = (
        Index("ix_physical_object_owner_hash", "owner_tenant_id", "sha256", "size_bytes"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=gen_id)
    owner_tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenant.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sink_type: Mapped[str] = mapped_column(String(32), nullable=False, default="s3")
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
