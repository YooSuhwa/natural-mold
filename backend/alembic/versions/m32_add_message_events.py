"""M32: ``message_events`` — SSE event trace storage per assistant turn.

Revision ID: m32_add_message_events
Revises: m31_share_links_active_unique
Create Date: 2026-05-03

Foundation for **W5 TraceStorage** — stores the full SSE event sequence
emitted during one ``stream_agent_response`` call, keyed by the assistant
message id.

Downstream consumers:
- **W3-out** GET-based stream resume: replay events with id > last_event_id.
- **W6** shared page chip rendering: read all turns for a conversation and
  reconstruct tool/skill chips on the public read-only view.

One row per assistant turn (regardless of branch). Rows survive edits and
regenerates — each new generation is a new row, ordered by ``created_at``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m32_add_message_events"
down_revision = "m31_share_links_active_unique"
branch_labels = None
depends_on = None


def _utc_now_default() -> sa.TextClause:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _uuid_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.UUID(as_uuid=True)
    return sa.String(36)


def upgrade() -> None:
    op.create_table(
        "message_events",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column(
            "conversation_id",
            _uuid_type(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assistant_msg_id",
            sa.String(64),
            nullable=False,
            unique=True,
        ),
        sa.Column("events", sa.JSON, nullable=False),
        sa.Column("last_event_id", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=_utc_now_default(),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=False),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_message_events_conv_created",
        "message_events",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_message_events_conv_created", table_name="message_events")
    op.drop_table("message_events")
