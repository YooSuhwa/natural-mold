"""M6: add credentials table + tool/mcp_server credential_id FK

Revision ID: m6_add_credentials
Revises: m5_add_llm_providers
Create Date: 2026-04-10
"""

import sqlalchemy as sa

from alembic import op

revision = "m6_add_credentials"
down_revision = "c4a9f1e2b387"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("credential_type", sa.String(20), nullable=False),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("data_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "tools",
        sa.Column("credential_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tools_credential_id",
        "tools",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "mcp_servers",
        sa.Column("credential_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_mcp_servers_credential_id",
        "mcp_servers",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_mcp_servers_credential_id", "mcp_servers", type_="foreignkey"
    )
    op.drop_column("mcp_servers", "credential_id")

    op.drop_constraint(
        "fk_tools_credential_id", "tools", type_="foreignkey"
    )
    op.drop_column("tools", "credential_id")

    op.drop_table("credentials")
