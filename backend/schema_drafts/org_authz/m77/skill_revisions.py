"""Frozen m77 skill_revisions fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_revisions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("source_session_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("revision_number", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("operation", sa.String(length=40), nullable=False, primary_key=False),
        sa.Column("skill_version", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column(
            "storage_provider",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="local",
            server_default="local",
        ),
        sa.Column("object_key", sa.String(length=800), nullable=False, primary_key=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, primary_key=False, default=0),
        sa.Column("file_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("changed_files", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("changelog_summary", sa.Text(), nullable=True, primary_key=False),
        sa.Column("changelog_items", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("compatibility_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("evaluation_summary", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, primary_key=False, default=dict),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["skill_builder_sessions.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["restored_from_revision_id"], ["skill_revisions.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"], ["skill_revisions.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"),
    )
    sa.Index(
        "ix_skill_revisions_skill_created",
        metadata.tables["skill_revisions"].c["skill_id"],
        metadata.tables["skill_revisions"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_skill_revisions_user_created",
        metadata.tables["skill_revisions"].c["user_id"],
        metadata.tables["skill_revisions"].c["created_at"],
        unique=False,
    )
