"""Frozen m77 marketplace_installations fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "marketplace_installations",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("item_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("version_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("installed_agent_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("installed_agent_blueprint_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("installed_mcp_server_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("installed_skill_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "install_status",
            sa.String(length=30),
            nullable=False,
            primary_key=False,
            default="active",
        ),
        sa.Column("is_dirty", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.CheckConstraint(
            (
                "(resource_type = 'agent'  AND ((installed_agent_blueprint_id IS NOT "
                "NULL        AND installed_agent_id IS NULL)       OR "
                "(installed_agent_id IS NOT NULL           AND "
                "installed_agent_blueprint_id IS NULL))  AND installed_mcp_server_id "
                "IS NULL AND installed_skill_id IS NULL) OR (resource_type = 'mcp' AND "
                "installed_mcp_server_id IS NOT NULL  AND installed_agent_id IS NULL "
                "AND installed_agent_blueprint_id IS NULL  AND installed_skill_id IS "
                "NULL) OR (resource_type = 'skill' AND installed_skill_id IS NOT NULL  "
                "AND installed_agent_id IS NULL AND installed_agent_blueprint_id IS "
                "NULL  AND installed_mcp_server_id IS NULL)"
            ),
            name="ck_marketplace_install_resource_target",
        ),
        sa.CheckConstraint(
            "install_status IN ('active','needs_setup','disabled','uninstalled')",
            name="ck_marketplace_install_status",
        ),
        sa.ForeignKeyConstraint(
            ["installed_skill_id"], ["skills.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["installed_mcp_server_id"], ["mcp_servers.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["marketplace_versions.id"], name=None, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["marketplace_items.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["installed_agent_id"], ["agents.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["installed_agent_blueprint_id"], ["agent_blueprints.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_marketplace_install_user_item",
        metadata.tables["marketplace_installations"].c["user_id"],
        metadata.tables["marketplace_installations"].c["item_id"],
        unique=False,
    )
    sa.Index(
        "ix_marketplace_install_user_resource",
        metadata.tables["marketplace_installations"].c["user_id"],
        metadata.tables["marketplace_installations"].c["resource_type"],
        unique=False,
    )
