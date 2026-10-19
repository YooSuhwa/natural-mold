"""Frozen m77 conversation_artifacts fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversation_artifacts",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("assistant_msg_id", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True, primary_key=False),
        sa.Column("source_tool_name", sa.String(length=120), nullable=True, primary_key=False),
        sa.Column("logical_path", sa.String(length=500), nullable=False, primary_key=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, primary_key=False),
        sa.Column("extension", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, primary_key=False),
        sa.Column(
            "artifact_kind",
            sa.String(length=30),
            nullable=False,
            primary_key=False,
            default="other",
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column("sha256", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="ready"
        ),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("last_opened_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("preview_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("download_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("branch_checkpoint_id", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("linked_message_ids", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, primary_key=False, default=dict),
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
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "conversation_id",
            "assistant_msg_id",
            "logical_path",
            name="uq_conversation_artifacts_turn_path",
        ),
    )
    sa.Index(
        "ix_conversation_artifacts_conversation_turn_updated",
        metadata.tables["conversation_artifacts"].c["conversation_id"],
        metadata.tables["conversation_artifacts"].c["assistant_msg_id"],
        metadata.tables["conversation_artifacts"].c["updated_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_artifacts_user_agent_created",
        metadata.tables["conversation_artifacts"].c["user_id"],
        metadata.tables["conversation_artifacts"].c["agent_id"],
        metadata.tables["conversation_artifacts"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_artifacts_user_conversation_created",
        metadata.tables["conversation_artifacts"].c["user_id"],
        metadata.tables["conversation_artifacts"].c["conversation_id"],
        metadata.tables["conversation_artifacts"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_artifacts_user_created",
        metadata.tables["conversation_artifacts"].c["user_id"],
        metadata.tables["conversation_artifacts"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_artifacts_user_favorite_created",
        metadata.tables["conversation_artifacts"].c["user_id"],
        metadata.tables["conversation_artifacts"].c["created_at"],
        unique=False,
        sqlite_where=sa.text("is_favorite = 1"),
        postgresql_where=sa.text("is_favorite = true"),
    )
    sa.Index(
        "ix_conversation_artifacts_user_kind_created",
        metadata.tables["conversation_artifacts"].c["user_id"],
        metadata.tables["conversation_artifacts"].c["artifact_kind"],
        metadata.tables["conversation_artifacts"].c["created_at"],
        unique=False,
    )
