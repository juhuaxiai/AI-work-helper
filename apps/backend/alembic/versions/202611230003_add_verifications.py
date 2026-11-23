"""Persist sandbox verification plans and approvals."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611230003"
down_revision = "202611230002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aiops_verification_plans",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("diagnostic_id", sa.String(80), nullable=False, unique=True),
        sa.Column("tenant_key", sa.String(160), nullable=False),
        sa.Column("requester_id", sa.String(80), nullable=False),
        sa.Column("template", sa.String(80), nullable=False),
        sa.Column("template_version", sa.Integer, nullable=False),
        sa.Column("trace_id", sa.String(40), nullable=False),
        sa.Column("evidence_id", sa.String(80), nullable=False),
        sa.Column("image_id", sa.String(80), nullable=False),
        sa.Column("execution_id", sa.String(40), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("approval_actor_id", sa.String(80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.String(80), nullable=True, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("container_cleaned", sa.Boolean, nullable=False),
    )
    op.create_index(
        "ix_aiops_verifications_tenant_updated",
        "aiops_verification_plans", ["tenant_key", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("aiops_verification_plans")
