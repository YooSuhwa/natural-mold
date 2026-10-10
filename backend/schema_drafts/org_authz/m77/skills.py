"""Frozen m77 skills fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skills",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("slug", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="text",
            server_default="text",
        ),
        sa.Column("storage_path", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column(
            "size_bytes",
            sa.Integer(),
            nullable=False,
            primary_key=False,
            default=0,
            server_default="0",
        ),
        sa.Column("version", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("package_metadata", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column(
            "used_by_count",
            sa.Integer(),
            nullable=False,
            primary_key=False,
            default=0,
            server_default="0",
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column("source_kind", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("source_marketplace_item_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source_marketplace_version_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source_commit", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("credential_requirements", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("execution_profile", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "origin_kind",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="created_by_me",
            server_default="created_by_me",
        ),
        sa.Column("origin_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("origin_marketplace_item_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("origin_marketplace_version_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "is_dirty",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column(
            "last_modified_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["origin_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_marketplace_version_id"],
            ["marketplace_versions.id"],
            name=None,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_marketplace_item_id"], ["marketplace_items.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["origin_marketplace_item_id"], ["marketplace_items.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["origin_marketplace_version_id"],
            ["marketplace_versions.id"],
            name=None,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("user_id", "slug", name="uq_skills_user_slug"),
    )
