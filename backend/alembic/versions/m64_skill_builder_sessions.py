from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m64_skill_builder_sessions"
down_revision = "m63_chat_navigator_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("current_revision_id", sa.Uuid(), nullable=True))
    op.create_table(
        "skill_builder_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="create"),
        sa.Column("source_skill_id", sa.Uuid(), nullable=True),
        sa.Column("base_skill_version", sa.String(length=40), nullable=True),
        sa.Column("base_content_hash", sa.String(length=64), nullable=True),
        sa.Column("base_snapshot", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="collecting"),
        sa.Column("current_phase", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages", sa.JSON(), nullable=True),
        sa.Column("intent", sa.JSON(), nullable=True),
        sa.Column("draft_package", sa.JSON(), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("compatibility_result", sa.JSON(), nullable=True),
        sa.Column("changelog_draft", sa.JSON(), nullable=True),
        sa.Column("eval_result", sa.JSON(), nullable=True),
        sa.Column("trigger_eval_result", sa.JSON(), nullable=True),
        sa.Column("finalized_skill_id", sa.Uuid(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["finalized_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_builder_sessions_user_updated",
        "skill_builder_sessions",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "ix_skill_builder_sessions_finalized_skill",
        "skill_builder_sessions",
        ["finalized_skill_id"],
    )
    op.create_index(
        "ix_skill_builder_sessions_source_skill",
        "skill_builder_sessions",
        ["source_skill_id"],
    )
    op.create_table(
        "skill_evaluation_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(length=40), nullable=False, server_default="builder"),
        sa.Column("template_key", sa.String(length=80), nullable=True),
        sa.Column("template_version", sa.String(length=40), nullable=True),
        sa.Column("generation_strategy", sa.JSON(), nullable=True),
        sa.Column("evals", sa.JSON(), nullable=False),
        sa.Column("expectations_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_evaluation_sets_skill_updated",
        "skill_evaluation_sets",
        ["skill_id", "updated_at"],
    )
    op.create_table(
        "skill_evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_set_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("skill_version", sa.String(length=40), nullable=True),
        sa.Column("skill_content_hash", sa.String(length=64), nullable=True),
        sa.Column("runner_model", sa.String(length=160), nullable=True),
        sa.Column("runner_version", sa.String(length=40), nullable=False, server_default="1"),
        sa.Column(
            "grader_prompt_version",
            sa.String(length=40),
            nullable=False,
            server_default="1",
        ),
        sa.Column("eval_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("run_config", sa.JSON(), nullable=True),
        sa.Column("estimate", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("benchmark", sa.JSON(), nullable=True),
        sa.Column("case_results", sa.JSON(), nullable=True),
        sa.Column("artifact_path", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["evaluation_set_id"],
            ["skill_evaluation_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_evaluation_runs_skill_created",
        "skill_evaluation_runs",
        ["skill_id", "created_at"],
    )
    op.create_index(
        "ix_skill_evaluation_runs_set_created",
        "skill_evaluation_runs",
        ["evaluation_set_id", "created_at"],
    )
    op.create_index(
        "ix_skill_evaluation_runs_status",
        "skill_evaluation_runs",
        ["status"],
    )
    op.create_table(
        "skill_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_session_id", sa.Uuid(), nullable=True),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
        sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("skill_version", sa.String(length=40), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("storage_provider", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("object_key", sa.String(length=800), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_files", sa.JSON(), nullable=True),
        sa.Column("changelog_summary", sa.Text(), nullable=True),
        sa.Column("changelog_items", sa.JSON(), nullable=True),
        sa.Column("compatibility_result", sa.JSON(), nullable=True),
        sa.Column("evaluation_summary", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_session_id"],
            ["skill_builder_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["skill_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["restored_from_revision_id"],
            ["skill_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"),
    )
    op.create_index(
        "ix_skill_revisions_skill_created",
        "skill_revisions",
        ["skill_id", "created_at"],
    )
    op.create_index(
        "ix_skill_revisions_user_created",
        "skill_revisions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_revisions_user_created", table_name="skill_revisions")
    op.drop_index("ix_skill_revisions_skill_created", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_index("ix_skill_evaluation_runs_status", table_name="skill_evaluation_runs")
    op.drop_index("ix_skill_evaluation_runs_set_created", table_name="skill_evaluation_runs")
    op.drop_index("ix_skill_evaluation_runs_skill_created", table_name="skill_evaluation_runs")
    op.drop_table("skill_evaluation_runs")
    op.drop_index("ix_skill_evaluation_sets_skill_updated", table_name="skill_evaluation_sets")
    op.drop_table("skill_evaluation_sets")
    op.drop_index("ix_skill_builder_sessions_source_skill", table_name="skill_builder_sessions")
    op.drop_index("ix_skill_builder_sessions_finalized_skill", table_name="skill_builder_sessions")
    op.drop_index("ix_skill_builder_sessions_user_updated", table_name="skill_builder_sessions")
    op.drop_table("skill_builder_sessions")
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_column("current_revision_id")
