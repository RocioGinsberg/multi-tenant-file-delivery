"""phase 6.5 workspace read view

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add workspace metadata tables and local demo tenants/workspaces."""
    tenant_table = sa.table(
        "tenant",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("tenant_type", sa.String),
        sa.column("parent_tenant_id", sa.String),
    )
    app_user_table = sa.table(
        "app_user",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("email", sa.String),
        sa.column("display_name", sa.String),
        sa.column("role", sa.String),
    )
    op.bulk_insert(
        tenant_table,
        [
            {
                "id": "subsidiary-a",
                "name": "爱施德",
                "tenant_type": "subsidiary",
                "parent_tenant_id": "hq",
            },
            {
                "id": "subsidiary-b",
                "name": "新燕海佳",
                "tenant_type": "subsidiary",
                "parent_tenant_id": "hq",
            },
        ],
    )
    op.bulk_insert(
        app_user_table,
        [
            {
                "id": "subsidiary-a-viewer",
                "tenant_id": "subsidiary-a",
                "email": "",
                "display_name": "Subsidiary A Viewer",
                "role": "subsidiary_viewer",
            },
            {
                "id": "subsidiary-b-viewer",
                "tenant_id": "subsidiary-b",
                "email": "",
                "display_name": "Subsidiary B Viewer",
                "role": "subsidiary_viewer",
            },
        ],
    )

    op.create_table(
        "workspace",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("owner_tenant_id", sa.String(32), nullable=False),
        sa.Column("target_tenant_id", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["owner_tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "owner_tenant_id",
            "target_key",
            name="uq_workspace_owner_target_key",
        ),
    )
    op.create_index("ix_workspace_owner_tenant", "workspace", ["owner_tenant_id"])
    op.create_index("ix_workspace_target_tenant", "workspace", ["target_tenant_id"])

    workspace_table = sa.table(
        "workspace",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("owner_tenant_id", sa.String),
        sa.column("target_tenant_id", sa.String),
        sa.column("target_key", sa.String),
    )
    op.bulk_insert(
        workspace_table,
        [
            {
                "id": "ws-aishide",
                "name": "爱施德 Workspace",
                "owner_tenant_id": "hq",
                "target_tenant_id": "subsidiary-a",
                "target_key": "aishide",
            },
            {
                "id": "ws-xinyan",
                "name": "新燕海佳 Workspace",
                "owner_tenant_id": "hq",
                "target_tenant_id": "subsidiary-b",
                "target_key": "xinyanhaijia",
            },
        ],
    )

    op.create_table(
        "physical_object",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner_tenant_id", sa.String(32), nullable=False),
        sa.Column("sink_type", sa.String(32), nullable=False, server_default="s3"),
        sa.Column("bucket", sa.String(128), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["owner_tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_physical_object_owner_hash",
        "physical_object",
        ["owner_tenant_id", "sha256", "size_bytes"],
    )

    op.create_table(
        "workspace_object",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("physical_object_id", sa.String(16), nullable=False),
        sa.Column("task_id", sa.String(16), nullable=False),
        sa.Column("task_item_id", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("dst_path", sa.String(1024), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(64), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["physical_object_id"], ["physical_object.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_item_id"], ["task_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_item_id", name="uq_workspace_object_task_item"),
    )
    op.create_index(
        "ix_workspace_object_workspace_uploaded",
        "workspace_object",
        ["workspace_id", "uploaded_at"],
    )

def downgrade() -> None:
    """Remove Phase 6.5 workspace read view."""
    op.drop_index("ix_workspace_object_workspace_uploaded", table_name="workspace_object")
    op.drop_table("workspace_object")

    op.drop_index("ix_physical_object_owner_hash", table_name="physical_object")
    op.drop_table("physical_object")

    op.drop_index("ix_workspace_target_tenant", table_name="workspace")
    op.drop_index("ix_workspace_owner_tenant", table_name="workspace")
    op.drop_table("workspace")

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM app_user WHERE id IN "
            "('subsidiary-a-viewer', 'subsidiary-b-viewer')"
        )
    )
    bind.execute(
        sa.text("DELETE FROM tenant WHERE id IN ('subsidiary-a', 'subsidiary-b')")
    )
