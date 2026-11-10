"""A tool's own approval default (tool-governance-plan 3.3, T2).

``approval_default`` on ``mcp_servers``, ``mcp_tools`` and ``tools``: ``{"mode", "rule"}`` or
NULL (no default — the risk level decides, as before).

Downgrade drops the columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i36_tool_approval_defaults"
down_revision = "i35_conversation_tool_grants"
branch_labels = None
depends_on = None

_TABLES = ("mcp_servers", "mcp_tools", "tools")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("approval_default", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "approval_default")
