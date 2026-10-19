"""Frozen m77 marketplace_versions fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "marketplace_versions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("item_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("version_label", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("version_number", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("payload_kind", sa.String(length=40), nullable=False, primary_key=False),
        sa.Column("payload", sa.JSON(), nullable=False, primary_key=False, default=dict),
        sa.Column("storage_path", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("credential_requirements", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("dependency_requirements", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("execution_profile", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("release_notes", sa.Text(), nullable=True, primary_key=False),
        sa.Column("source_commit", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("source_ref", sa.String(length=120), nullable=True, primary_key=False),
        sa.Column("source_path", sa.Text(), nullable=True, primary_key=False),
        sa.Column("created_by", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.CheckConstraint(
            "payload_kind IN ('skill_package','agent_spec','mcp_template')",
            name="ck_marketplace_payload_kind",
        ),
        sa.CheckConstraint(
            "resource_type IN ('agent','mcp','skill')", name="ck_marketplace_version_resource_type"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["marketplace_items.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "item_id", "version_number", name="uq_marketplace_versions_item_number"
        ),
    )
    sa.Index(
        "ix_marketplace_versions_content_hash",
        metadata.tables["marketplace_versions"].c["content_hash"],
        unique=False,
    )
