"""add middleware_configs to agents

Revision ID: dcb1dff2e64d
Revises: b1dae1c5b83a
Create Date: 2026-04-05 12:52:22.273442

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dcb1dff2e64d"
down_revision: str | Sequence[str] | None = "b1dae1c5b83a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agents", sa.Column("middleware_configs", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("agents", "middleware_configs")
