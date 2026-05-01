"""M26: MCP server health polling fields + ``is_system`` flag, MCP tool
``last_seen_at`` for stale-tool detection.

Revision ID: m26_mcp_health_and_system
Revises: m25_add_agent_mcp_tools
Create Date: 2026-05-01

Adds the columns needed for:
- Periodic health polling of MCP servers (results surfaced in the list API
  + detail dialog without forcing a synchronous probe).
- Marking servers as system-managed catalogue entries (precursor to a future
  admin/user split — for now it just renders a "system" badge and prevents
  user-side deletion of a row that the seed/registry promotes).
- Detecting tools that vanish between discovery runs without dropping the
  ORM row immediately — the UI shows a "stale" badge so users can decide.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m26_mcp_health_and_system"
down_revision = "m25_add_agent_mcp_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch:
        batch.add_column(
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("health_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("health_polled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("health_message", sa.Text(), nullable=True))

    with op.batch_alter_table("mcp_tools") as batch:
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mcp_tools") as batch:
        batch.drop_column("last_seen_at")

    with op.batch_alter_table("mcp_servers") as batch:
        batch.drop_column("health_message")
        batch.drop_column("health_polled_at")
        batch.drop_column("health_status")
        batch.drop_column("is_system")
