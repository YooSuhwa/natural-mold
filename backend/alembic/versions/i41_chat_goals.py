"""Store conversation goals, evaluation receipts and transactional notification intents."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i41_chat_goals"
down_revision = "i40_chat_run_steers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_goals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT", name="fk_conversation_goals_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            ondelete="RESTRICT",
            name="fk_conversation_goals_org_tenant",
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("auto_followups", sa.Integer(), nullable=False),
        sa.Column("no_progress_count", sa.Integer(), nullable=False),
        sa.Column("baseline_steer_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_steer_seq", sa.Integer(), nullable=False),
        sa.Column("last_evidence", sa.JSON(), nullable=True),
        sa.Column("blocked_reason", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_goals_conversation"),
        sa.CheckConstraint(
            "status IN ('active','paused','blocked','completed','canceled')",
            name="ck_conversation_goals_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND revision >= 1 AND auto_followups >= 0 "
            "AND no_progress_count >= 0 AND latest_steer_seq >= 0 AND baseline_steer_seq >= 0",
            name="ck_conversation_goals_counters",
        ),
    )
    op.create_table(
        "goal_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "goal_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_version", sa.Integer(), nullable=False),
        sa.Column("requires_fresh_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("steer_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("followup_ordinal", sa.Integer(), nullable=True),
        sa.Column(
            "followup_input_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_run_inputs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "goal_id", "goal_version", "run_id", name="uq_goal_attempts_evaluation"
        ),
        sa.UniqueConstraint(
            "goal_id", "goal_version", "followup_ordinal", name="uq_goal_attempts_followup"
        ),
        sa.UniqueConstraint("followup_input_id", name="uq_goal_attempts_input"),
        sa.CheckConstraint(
            "status IN ('pending','completed','followup_reserved','superseded','blocked')",
            name="ck_goal_attempts_status",
        ),
        sa.CheckConstraint(
            "goal_version >= 1 AND steer_seq >= 0 AND "
            "(followup_ordinal IS NULL OR followup_ordinal > 0)",
            name="ck_goal_attempts_counters",
        ),
    )
    op.create_index("ix_goal_attempts_run", "goal_attempts", ["run_id"])
    op.create_index("ix_goal_attempts_status_created", "goal_attempts", ["status", "created_at"])
    op.create_table(
        "goal_transition_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "goal_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal_version", sa.Integer(), nullable=False),
        sa.Column("goal_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "goal_id",
            "goal_version",
            "goal_revision",
            "kind",
            name="uq_goal_transition_intents_event",
        ),
        sa.CheckConstraint(
            "kind IN ('completed','blocked','approval_requested')",
            name="ck_goal_transition_intents_kind",
        ),
        sa.CheckConstraint(
            "goal_version >= 1 AND goal_revision >= 1", name="ck_goal_transition_intents_version"
        ),
    )
    op.create_index(
        "ix_goal_transition_intents_created", "goal_transition_intents", ["created_at", "id"]
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        tables = ("conversation_goals", "goal_attempts", "goal_transition_intents")
        quote = bind.dialect.identifier_preparer.quote
        for name in tables:
            for statement in create_policy_sql(quote, table_policy(quote, name)):
                op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, tables):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table("goal_transition_intents")
    op.drop_table("goal_attempts")
    op.drop_table("conversation_goals")
