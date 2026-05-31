"""M52: marketplace item Lucide icon id.

Revision ID: m52_marketplace_item_icon_id
Revises: m51_msg_events_ext_trace
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m52_marketplace_item_icon_id"
down_revision = "m51_msg_events_ext_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_items",
        sa.Column("icon_id", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketplace_items", "icon_id")
