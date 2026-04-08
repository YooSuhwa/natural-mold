"""add_builder_sessions_table

Revision ID: 7c8b80bbda62
Revises: m5_add_llm_providers
Create Date: 2026-04-08 08:21:55.187246

"""

import sqlalchemy as sa

from alembic import op

revision = "7c8b80bbda62"
down_revision = "m5_add_llm_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "builder_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_phase", sa.Integer(), nullable=False),
        sa.Column("project_path", sa.String(length=500), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=True),
        sa.Column("tools_result", sa.JSON(), nullable=True),
        sa.Column("middlewares_result", sa.JSON(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("draft_config", sa.JSON(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("builder_sessions")
