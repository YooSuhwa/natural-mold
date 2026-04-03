"""add agent is_favorite model_params and tool tags

Revision ID: f73536bd0236
Revises: b4e1a2f3c5d7
Create Date: 2026-04-02 20:27:22.688384

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f73536bd0236'
down_revision: Union[str, Sequence[str], None] = 'b4e1a2f3c5d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('agents', sa.Column('is_favorite', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('agents', sa.Column('model_params', sa.JSON(), nullable=True))
    op.add_column('tools', sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tools', 'tags')
    op.drop_column('agents', 'model_params')
    op.drop_column('agents', 'is_favorite')
