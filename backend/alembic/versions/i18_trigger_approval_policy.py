"""Schedule approval policy (feature plan v2 순번 13, 17.3; decision D27).

``agent_triggers.approval_policy``: what a scheduled run does with a tool call that needs a
person's approval. ``inbox`` (the default for new schedules) sends it to the approval
inbox and waits; ``blocked`` answers a BLOCKED result for that call and the run goes on.
Existing schedules become ``blocked``, the closest to what they did (no such call was ever
made; the whole run failed before the model started) without starting to wait on anyone;
the schedule screen tells their owners they can switch. Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i18_trigger_approval_policy"
down_revision = "i17_approval_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_triggers") as batch:
        batch.add_column(
            sa.Column("approval_policy", sa.String(20), nullable=False, server_default="blocked")
        )
    with op.batch_alter_table("agent_triggers") as batch:
        batch.alter_column(
            "approval_policy",
            existing_type=sa.String(20),
            existing_nullable=False,
            server_default="inbox",
        )
        batch.create_check_constraint(
            "ck_agent_triggers_approval_policy", "approval_policy IN ('inbox', 'blocked')"
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_triggers") as batch:
        batch.drop_constraint("ck_agent_triggers_approval_policy", type_="check")
        batch.drop_column("approval_policy")
