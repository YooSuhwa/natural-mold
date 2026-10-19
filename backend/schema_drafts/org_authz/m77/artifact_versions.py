"""Frozen m77 artifact_versions fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "artifact_versions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("artifact_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("version_number", sa.Integer(), nullable=False, primary_key=False),
        sa.Column(
            "storage_provider",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="local",
        ),
        sa.Column("bucket", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("object_key", sa.String(length=800), nullable=False, primary_key=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False, primary_key=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column("sha256", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, primary_key=False, default=dict),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["conversation_artifacts.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions_number"),
    )
    sa.Index(
        "ix_artifact_versions_artifact_created",
        metadata.tables["artifact_versions"].c["artifact_id"],
        metadata.tables["artifact_versions"].c["created_at"],
        unique=False,
    )
