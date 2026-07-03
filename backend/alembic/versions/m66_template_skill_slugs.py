from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m66_template_skill_slugs"
down_revision = "m65_widen_stream_event_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("recommended_skill_slugs", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("templates", "recommended_skill_slugs")
