"""M61: installed agent blueprints.

Revision ID: m61_agent_blueprints
Revises: m61_conversation_runs
Create Date: 2026-06-10

Re-chained onto m61_conversation_runs during the main merge so the
agent-blueprint migrations form a single linear head with the
conversation-run lifecycle table that landed on main in parallel.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m61_agent_blueprints"
down_revision = "m61_conversation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_blueprints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon_id", sa.String(length=80), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=False),
        sa.Column("source_marketplace_item_id", sa.Uuid(), nullable=True),
        sa.Column("source_marketplace_version_id", sa.Uuid(), nullable=True),
        sa.Column("origin_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "origin_kind",
            sa.String(length=40),
            nullable=False,
            server_default="imported_by_me",
        ),
        sa.Column(
            "install_status",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
        sa.Column("is_dirty", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_agent_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_marketplace_item_id"],
            ["marketplace_items.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_marketplace_version_id"],
            ["marketplace_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["origin_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_blueprints_user_updated",
        "agent_blueprints",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "ix_agent_blueprints_source_item",
        "agent_blueprints",
        ["source_marketplace_item_id"],
    )

    op.add_column(
        "marketplace_installations",
        sa.Column("installed_agent_blueprint_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_marketplace_installations_agent_blueprint",
        "marketplace_installations",
        "agent_blueprints",
        ["installed_agent_blueprint_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "ck_marketplace_install_resource_target",
        "marketplace_installations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_marketplace_install_resource_target",
        "marketplace_installations",
        (
            "(resource_type = 'agent' "
            " AND ((installed_agent_blueprint_id IS NOT NULL "
            "       AND installed_agent_id IS NULL) "
            "      OR (installed_agent_id IS NOT NULL "
            "          AND installed_agent_blueprint_id IS NULL)) "
            " AND installed_mcp_server_id IS NULL AND installed_skill_id IS NULL) "
            "OR (resource_type = 'mcp' AND installed_mcp_server_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_agent_blueprint_id IS NULL "
            " AND installed_skill_id IS NULL) "
            "OR (resource_type = 'skill' AND installed_skill_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_agent_blueprint_id IS NULL "
            " AND installed_mcp_server_id IS NULL)"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_marketplace_install_resource_target",
        "marketplace_installations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_marketplace_install_resource_target",
        "marketplace_installations",
        (
            "(resource_type = 'agent' AND installed_agent_id IS NOT NULL "
            " AND installed_mcp_server_id IS NULL AND installed_skill_id IS NULL) "
            "OR (resource_type = 'mcp' AND installed_mcp_server_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_skill_id IS NULL) "
            "OR (resource_type = 'skill' AND installed_skill_id IS NOT NULL "
            " AND installed_agent_id IS NULL AND installed_mcp_server_id IS NULL)"
        ),
    )
    op.drop_constraint(
        "fk_marketplace_installations_agent_blueprint",
        "marketplace_installations",
        type_="foreignkey",
    )
    op.drop_column("marketplace_installations", "installed_agent_blueprint_id")
    op.drop_index("ix_agent_blueprints_source_item", table_name="agent_blueprints")
    op.drop_index("ix_agent_blueprints_user_updated", table_name="agent_blueprints")
    op.drop_table("agent_blueprints")
