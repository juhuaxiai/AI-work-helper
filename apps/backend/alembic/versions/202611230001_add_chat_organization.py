"""Persist the organization of new chat sessions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202611230001"
down_revision = "202607110007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("organization_key", sa.String(160), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "organization_key")
