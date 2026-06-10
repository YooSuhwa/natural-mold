"""M60: persistent credential oauth states.

Revision ID: m60_credential_oauth_states
Revises: m59_conversation_artifacts
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m60_credential_oauth_states"
down_revision = "m59_conversation_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("nonce", sa.String(length=128), nullable=True),
        sa.Column("origin", sa.String(length=40), nullable=False),
        sa.Column("return_to", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_credential_oauth_states_state_hash"),
    )
    op.create_index(
        "ix_credential_oauth_states_credential_created",
        "credential_oauth_states",
        ["credential_id", "created_at"],
    )
    op.create_index(
        "ix_credential_oauth_states_user_expires",
        "credential_oauth_states",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_credential_oauth_states_user_expires", table_name="credential_oauth_states")
    op.drop_index(
        "ix_credential_oauth_states_credential_created",
        table_name="credential_oauth_states",
    )
    op.drop_table("credential_oauth_states")
