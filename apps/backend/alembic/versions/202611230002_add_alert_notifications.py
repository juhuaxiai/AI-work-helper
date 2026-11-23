"""Persist Alertmanager events by source, tenant and occurrence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611230002"
down_revision = "202611230001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aiops_alert_notifications",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_key", sa.String(160), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("labels", sa.JSON, nullable=False),
        sa.Column("diagnostic_id", sa.String(80), nullable=True),
        sa.Column("diagnostic_owner_id", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_aiops_alert_notifications_tenant_received",
        "aiops_alert_notifications",
        ["tenant_key", "received_at"],
    )


def downgrade() -> None:
    op.drop_table("aiops_alert_notifications")
