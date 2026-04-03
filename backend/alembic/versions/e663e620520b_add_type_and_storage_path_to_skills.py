"""add_type_and_storage_path_to_skills

Revision ID: e663e620520b
Revises: 60843b94d265
Create Date: 2026-04-03 20:07:45.759408

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e663e620520b"
down_revision: str | Sequence[str] | None = "60843b94d265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "skills",
        sa.Column("type", sa.String(length=20), server_default="text", nullable=False),
    )
    op.add_column("skills", sa.Column("storage_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("skills", "storage_path")
    op.drop_column("skills", "type")
