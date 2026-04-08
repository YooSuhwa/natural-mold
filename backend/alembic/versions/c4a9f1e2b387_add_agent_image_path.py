"""add agent image_path

Revision ID: c4a9f1e2b387
Revises: 7c8b80bbda62
Create Date: 2026-04-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a9f1e2b387"
down_revision: str | Sequence[str] | None = "7c8b80bbda62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agents", sa.Column("image_path", sa.String(500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("agents", "image_path")
