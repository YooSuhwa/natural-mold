"""M49: add selected schedule conversation target.

Revision ID: m49_schedule_conversation_policy
Revises: m48_schedule_guardrails
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m49_schedule_conversation_policy"
down_revision = "m48_schedule_guardrails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_triggers",
        sa.Column("target_conversation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_triggers_target_conversation_id_conversations",
        "agent_triggers",
        "conversations",
        ["target_conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_triggers_target_conversation_id_conversations",
        "agent_triggers",
        type_="foreignkey",
    )
    op.drop_column("agent_triggers", "target_conversation_id")
