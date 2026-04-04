"""add_conversation_is_pinned

Revision ID: b1dae1c5b83a
Revises: e663e620520b
Create Date: 2026-04-04 11:58:08.096627

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1dae1c5b83a"
down_revision: str | Sequence[str] | None = "e663e620520b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "conversations",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("conversations", "is_pinned")
