"""phase 6 multitenancy and actor ownership

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenant/app_user baseline and task ownership columns."""
    op.create_table(
        "tenant",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("tenant_type", sa.String(32), nullable=False, server_default="subsidiary"),
        sa.Column("parent_tenant_id", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["parent_tenant_id"], ["tenant.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("role", sa.String(32), nullable=False, server_default="subsidiary_viewer"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_app_user_tenant_role", "app_user", ["tenant_id", "role"])

    tenant_table = sa.table(
        "tenant",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("tenant_type", sa.String),
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
        [{"id": "hq", "name": "HQ", "tenant_type": "hq"}],
    )
    op.bulk_insert(
        app_user_table,
        [{
            "id": "local-user",
            "tenant_id": "hq",
            "email": "",
            "display_name": "Local User",
            "role": "hq_uploader",
        }],
    )

    bind = op.get_bind()
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("task", naming_convention=naming_convention) as batch_op:
        batch_op.add_column(
            sa.Column(
                "owner_tenant_id",
                sa.String(32),
                nullable=False,
                server_default="hq",
            )
        )
        batch_op.add_column(
            sa.Column(
                "owner_user_id",
                sa.String(64),
                nullable=False,
                server_default="local-user",
            )
        )
        _drop_global_idempotency_unique(batch_op, bind)
        batch_op.create_unique_constraint(
            "uq_task_owner_tenant_idempotency",
            ["owner_tenant_id", "idempotency_key"],
        )
        batch_op.create_foreign_key(
            "fk_task_owner_tenant_id_tenant",
            "tenant",
            ["owner_tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_task_owner_user_id_app_user",
            "app_user",
            ["owner_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_task_owner_tenant_id", ["owner_tenant_id"])
        batch_op.create_index("ix_task_owner_tenant_user", ["owner_tenant_id", "owner_user_id"])


def downgrade() -> None:
    """Remove Phase 6 multitenancy baseline."""
    with op.batch_alter_table("task") as batch_op:
        batch_op.drop_index("ix_task_owner_tenant_user")
        batch_op.drop_index("ix_task_owner_tenant_id")
        batch_op.drop_constraint("uq_task_owner_tenant_idempotency", type_="unique")
        batch_op.drop_constraint("fk_task_owner_user_id_app_user", type_="foreignkey")
        batch_op.drop_constraint("fk_task_owner_tenant_id_tenant", type_="foreignkey")
        batch_op.drop_column("owner_user_id")
        batch_op.drop_column("owner_tenant_id")
        batch_op.create_unique_constraint("uq_task_idempotency_key", ["idempotency_key"])

    op.drop_index("ix_app_user_tenant_role", table_name="app_user")
    op.drop_table("app_user")
    op.drop_table("tenant")


def _drop_global_idempotency_unique(batch_op, bind) -> None:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints("task"):
        if constraint.get("column_names") == ["idempotency_key"] and constraint.get("name"):
            batch_op.drop_constraint(constraint["name"], type_="unique")
            return

    batch_op.drop_constraint("uq_task_idempotency_key", type_="unique")
