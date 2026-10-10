"""Frozen m77 marketplace_items fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "marketplace_items",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("resource_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("is_listed", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("name", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("slug", sa.String(length=220), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("icon_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("icon_url", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "visibility", sa.String(length=20), nullable=False, primary_key=False, default="private"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="draft"
        ),
        sa.Column(
            "moderation_status",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="approved",
        ),
        sa.Column("source_kind", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("source_url", sa.Text(), nullable=True, primary_key=False),
        sa.Column("source_external_id", sa.String(length=240), nullable=True, primary_key=False),
        sa.Column("latest_version_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("tags", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("categories", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("locale", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("metadata", sa.JSON(), nullable=True, primary_key=False),
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
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.CheckConstraint(
            "resource_type IN ('agent','mcp','skill')", name="ck_marketplace_resource_type"
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','deprecated','disabled')", name="ck_marketplace_status"
        ),
        sa.CheckConstraint(
            "(is_system = false) OR (owner_user_id IS NULL)", name="ck_marketplace_system_owner"
        ),
        sa.CheckConstraint(
            "visibility IN ('private','restricted','public','unlisted','system')",
            name="ck_marketplace_visibility",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["latest_version_id"],
            ["marketplace_versions.id"],
            name="fk_marketplace_items_latest_version",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_marketplace_items_listed",
        metadata.tables["marketplace_items"].c["is_listed"],
        metadata.tables["marketplace_items"].c["visibility"],
        metadata.tables["marketplace_items"].c["status"],
        unique=False,
    )
