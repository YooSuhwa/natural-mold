"""add_agent_tools_config

Revision ID: b4e1a2f3c5d7
Revises: 9e6e7f63c3b6
Create Date: 2026-04-02 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e1a2f3c5d7'
down_revision: Union[str, None] = '9e6e7f63c3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_tools', sa.Column('config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_tools', 'config')
