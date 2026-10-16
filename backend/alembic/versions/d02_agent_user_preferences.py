"""D-02 (m86 part, early): per-user agent favorites in ``agent_user_preferences``.

Only non-owners (viewers of shared agents) store rows here. Owners keep
``agents.is_favorite`` as the source of truth until P8, whose migration copies that
column into this table before dropping it, so no backfill happens now.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d02_agent_user_preferences"
down_revision = "c04_outbox_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_user_preferences_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_user_preferences_agent_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "agent_id", name="pk_agent_user_preferences"),
    )


def downgrade() -> None:
    op.drop_table("agent_user_preferences")
