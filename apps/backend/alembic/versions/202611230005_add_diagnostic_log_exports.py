"""Persist limited, short-lived diagnostic log file references."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611230005"
down_revision = "202611230004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aiops_diagnostic_log_exports",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(80),
            sa.ForeignKey("aiops_diagnostic_tasks.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.String(80), nullable=False),
        sa.Column("tenant_key", sa.String(160), nullable=False),
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_aiops_diagnostic_log_exports_expires_at", "aiops_diagnostic_log_exports", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aiops_diagnostic_log_exports_expires_at", table_name="aiops_diagnostic_log_exports"
    )
    op.drop_table("aiops_diagnostic_log_exports")
