"""Add nullable versioned runtime policy storage to agents.

Existing rows intentionally remain ``NULL`` so the resolver can preserve the
pre-M71 runtime graph without a data backfill.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m71_runtime_policy"
down_revision = "m70_skill_usage_and_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("runtime_policy", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("runtime_policy")
