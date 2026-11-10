"""A skill's own approval default (tool-governance-plan 3.8, T6).

``approval_default`` on ``skills``: ``{"mode", "rule"}`` or NULL (no default — the risk
level decides, as before).

Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i39_skill_approval_default"
down_revision = "i38_registration_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("approval_default", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("skills", "approval_default")
