"""agents.runtime_profile — hidden runtime rows (skill-studio phase 1, AD-1).

``standard`` = normal user agents. Non-standard profiles (``skill_builder``)
are per-user hidden rows excluded from every enumeration surface.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m68_agents_runtime_profile"
down_revision = "m67_hotpath_fk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "runtime_profile",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("runtime_profile")
