"""Frozen m77 agent_blueprints fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_blueprints",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("icon_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("tags", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("categories", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("spec", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("credential_bindings", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("source_marketplace_item_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source_marketplace_version_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("origin_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "origin_kind",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="imported_by_me",
        ),
        sa.Column(
            "install_status",
            sa.String(length=30),
            nullable=False,
            primary_key=False,
            default="active",
        ),
        sa.Column("is_dirty", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column(
            "created_agent_count", sa.Integer(), nullable=False, primary_key=False, default=0
        ),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_marketplace_item_id"], ["marketplace_items.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_marketplace_version_id"],
            ["marketplace_versions.id"],
            name=None,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["origin_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
