"""M59: conversation artifacts.

Revision ID: m59_conversation_artifacts
Revises: m58_merge_memory_and_audit_heads
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m59_conversation_artifacts"
down_revision = "m58_merge_memory_and_audit_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_msg_id", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True),
        sa.Column("source_tool_name", sa.String(length=120), nullable=True),
        sa.Column("logical_path", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=40), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("artifact_kind", sa.String(length=30), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_opened_at", sa.DateTime(), nullable=True),
        sa.Column(
            "preview_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "download_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("branch_checkpoint_id", sa.String(length=64), nullable=True),
        sa.Column("linked_message_ids", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('writing', 'ready', 'deleted', 'failed')",
            name="ck_conversation_artifacts_status",
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('image', 'video', 'audio', 'pdf', 'markdown', 'html', "
            "'code', 'document', 'data', 'cad', 'other')",
            name="ck_conversation_artifacts_kind",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "assistant_msg_id",
            "logical_path",
            name="uq_conversation_artifacts_turn_path",
        ),
    )
    op.create_index(
        "ix_conversation_artifacts_user_conversation_created",
        "conversation_artifacts",
        ["user_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_artifacts_conversation_turn_updated",
        "conversation_artifacts",
        ["conversation_id", "assistant_msg_id", "updated_at"],
    )
    op.create_index(
        "ix_conversation_artifacts_user_created",
        "conversation_artifacts",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_artifacts_user_agent_created",
        "conversation_artifacts",
        ["user_id", "agent_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_artifacts_user_kind_created",
        "conversation_artifacts",
        ["user_id", "artifact_kind", "created_at"],
    )
    op.create_index(
        "ix_conversation_artifacts_user_favorite_created",
        "conversation_artifacts",
        ["user_id", "created_at"],
        postgresql_where=sa.text("is_favorite = true"),
        sqlite_where=sa.text("is_favorite = 1"),
    )

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("storage_provider", sa.String(length=20), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("object_key", sa.String(length=800), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "storage_provider IN ('local', 's3')",
            name="ck_artifact_versions_storage_provider",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["conversation_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions_number"),
    )
    op.create_index(
        "ix_artifact_versions_artifact_created",
        "artifact_versions",
        ["artifact_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_artifact_created", table_name="artifact_versions")
    op.drop_table("artifact_versions")
    op.drop_index(
        "ix_conversation_artifacts_user_favorite_created",
        table_name="conversation_artifacts",
    )
    op.drop_index(
        "ix_conversation_artifacts_user_kind_created",
        table_name="conversation_artifacts",
    )
    op.drop_index(
        "ix_conversation_artifacts_user_agent_created",
        table_name="conversation_artifacts",
    )
    op.drop_index("ix_conversation_artifacts_user_created", table_name="conversation_artifacts")
    op.drop_index(
        "ix_conversation_artifacts_conversation_turn_updated",
        table_name="conversation_artifacts",
    )
    op.drop_index(
        "ix_conversation_artifacts_user_conversation_created",
        table_name="conversation_artifacts",
    )
    op.drop_table("conversation_artifacts")
