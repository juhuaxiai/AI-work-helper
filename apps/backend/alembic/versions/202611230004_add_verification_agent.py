"""Link approvals to a durable Verification Agent thread."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611230004"
down_revision = "202611230003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("aiops_verification_plans", sa.Column("agent_thread_id", sa.String(240)))
    op.add_column("aiops_verification_plans", sa.Column("hypothesis", sa.Text))
    op.add_column("aiops_verification_plans", sa.Column("selection_reason", sa.Text))


def downgrade() -> None:
    for name in ("selection_reason", "hypothesis", "agent_thread_id"):
        op.drop_column("aiops_verification_plans", name)
