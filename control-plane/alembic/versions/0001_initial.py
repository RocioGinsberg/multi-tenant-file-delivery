"""initial schema (task, task_item, task_event)

Revision ID: 0001
Revises:
Create Date: 2026-05-10 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create task, task_item, and task_event tables with indexes and constraints."""
    # ── task ──────────────────────────────────────────────────────────────────
    op.create_table(
        "task",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("submission_label", sa.String(255), nullable=False, server_default=""),
        sa.Column("temp_dir", sa.String(512), nullable=False, server_default=""),
        sa.Column("summary_json", sa.JSON, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="local-user"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key"),
    )

    # ── task_item ─────────────────────────────────────────────────────────────
    op.create_table(
        "task_item",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("task_id", sa.String(16), nullable=False),
        sa.Column("src_path", sa.String(512), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("ext", sa.String(32), nullable=False, server_default=""),
        sa.Column("file_size", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("target_name_raw", sa.String(128), nullable=False, server_default=""),
        sa.Column("target_name_matched", sa.String(128), nullable=False, server_default=""),
        sa.Column("document_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("category_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("dst_dir", sa.String(512), nullable=False, server_default=""),
        sa.Column("dst_path", sa.String(1024), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_message", sa.String(1024), nullable=False, server_default=""),
        sa.Column("warning_message", sa.String(1024), nullable=False, server_default=""),
        sa.Column("upload_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("upload_error", sa.String(1024), nullable=False, server_default=""),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "src_path", name="uq_task_item_task_src"),
    )
    op.create_index("ix_task_item_task_id", "task_item", ["task_id"])
    op.create_index("ix_task_item_task_upload", "task_item", ["task_id", "upload_status"])

    # ── task_event ────────────────────────────────────────────────────────────
    op.create_table(
        "task_event",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("task_id", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_task_event_task_created", "task_event", ["task_id", "created_at"]
    )


def downgrade() -> None:
    """Drop task_event, task_item, and task tables in reverse dependency order."""
    op.drop_index("ix_task_event_task_created", table_name="task_event")
    op.drop_table("task_event")

    op.drop_index("ix_task_item_task_upload", table_name="task_item")
    op.drop_index("ix_task_item_task_id", table_name="task_item")
    op.drop_table("task_item")

    op.drop_table("task")
