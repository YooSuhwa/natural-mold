"""skill_builder_sessions v2 — builder-chat runtime columns (AD-6).

``conversation_id`` links the session to its hidden-agent conversation
(SET NULL so the session survives conversation deletion),
``draft_workspace_path`` is the ADR-018 relative workspace path, and
``tool_consents`` stores AD-4 scoped-consent records.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m69_skill_builder_sessions_v2"
down_revision = "m68_agents_runtime_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skill_builder_sessions") as batch_op:
        batch_op.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("draft_workspace_path", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("tool_consents", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            "fk_skill_builder_sessions_conversation",
            "conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_skill_builder_sessions_conversation",
        "skill_builder_sessions",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_builder_sessions_conversation",
        table_name="skill_builder_sessions",
    )
    with op.batch_alter_table("skill_builder_sessions") as batch_op:
        batch_op.drop_constraint("fk_skill_builder_sessions_conversation", type_="foreignkey")
        batch_op.drop_column("tool_consents")
        batch_op.drop_column("draft_workspace_path")
        batch_op.drop_column("conversation_id")
