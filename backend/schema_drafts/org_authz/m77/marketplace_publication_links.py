"""Frozen m77 marketplace_publication_links fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "marketplace_publication_links",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("item_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("source_agent_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source_mcp_server_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source_skill_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
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
            "resource_type IN ('agent','mcp','skill')", name="ck_pub_link_resource_type"
        ),
        sa.CheckConstraint(
            (
                "(resource_type = 'agent' AND source_agent_id IS NOT NULL  AND "
                "source_mcp_server_id IS NULL AND source_skill_id IS NULL) OR "
                "(resource_type = 'mcp' AND source_mcp_server_id IS NOT NULL  AND "
                "source_agent_id IS NULL AND source_skill_id IS NULL) OR "
                "(resource_type = 'skill' AND source_skill_id IS NOT NULL  AND "
                "source_agent_id IS NULL AND source_mcp_server_id IS NULL)"
            ),
            name="ck_pub_link_target",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["marketplace_items.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_mcp_server_id"], ["mcp_servers.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("item_id", name="uq_pub_link_item"),
    )
    sa.Index(
        "ix_pub_link_resource",
        metadata.tables["marketplace_publication_links"].c["user_id"],
        metadata.tables["marketplace_publication_links"].c["resource_type"],
        unique=False,
    )
