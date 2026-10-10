"""Frozen m77 skill_builder_sessions fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_builder_sessions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_request", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "mode",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="create",
            server_default="create",
        ),
        sa.Column("source_skill_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("base_skill_version", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("base_content_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("base_snapshot", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            primary_key=False,
            default="collecting",
            server_default="collecting",
        ),
        sa.Column(
            "current_phase",
            sa.Integer(),
            nullable=False,
            primary_key=False,
            default=0,
            server_default="0",
        ),
        sa.Column("messages", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("intent", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("draft_package", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("validation_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("compatibility_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("changelog_draft", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("eval_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("trigger_eval_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("finalized_skill_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("draft_workspace_path", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("tool_consents", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["finalized_skill_id"], ["skills.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_skill_id"], ["skills.id"], name=None, ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_skill_builder_sessions_conversation",
        metadata.tables["skill_builder_sessions"].c["conversation_id"],
        unique=False,
    )
    sa.Index(
        "ix_skill_builder_sessions_finalized_skill",
        metadata.tables["skill_builder_sessions"].c["finalized_skill_id"],
        unique=False,
    )
    sa.Index(
        "ix_skill_builder_sessions_source_skill",
        metadata.tables["skill_builder_sessions"].c["source_skill_id"],
        unique=False,
    )
    sa.Index(
        "ix_skill_builder_sessions_user_updated",
        metadata.tables["skill_builder_sessions"].c["user_id"],
        metadata.tables["skill_builder_sessions"].c["updated_at"],
        unique=False,
    )
