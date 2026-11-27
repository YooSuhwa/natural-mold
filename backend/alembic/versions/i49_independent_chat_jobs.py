"""Persist an independent child conversation alongside its accepted parent/child runs."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i49_independent_chat_jobs"
down_revision = "i39_skill_approval_default"
branch_labels = None
depends_on = None

_TABLE = "independent_chat_jobs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_agent_id", sa.Uuid(), nullable=False),
        sa.Column("parent_conversation_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=False),
        sa.Column("child_agent_id", sa.Uuid(), nullable=False),
        sa.Column("child_conversation_id", sa.Uuid(), nullable=False),
        sa.Column("child_run_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("capability_sha256", sa.String(64), nullable=False),
        sa.Column("source_child_task_id", sa.Uuid(), nullable=True),
        sa.Column("pending_approval_run_id", sa.Uuid(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_independent_chat_jobs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_independent_chat_jobs_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_independent_chat_jobs_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_independent_chat_jobs_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id", "parent_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_parent_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id", "child_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_parent_delegate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_run_id", "child_agent_id"],
            ["conversation_run_agents.run_id", "conversation_run_agents.agent_id_snapshot"],
            name="fk_independent_chat_jobs_child_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_conversation_id"],
            ["conversations.id"],
            name="fk_independent_chat_jobs_parent_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_conversation_id"],
            ["conversations.id"],
            name="fk_independent_chat_jobs_child_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pending_approval_run_id"],
            ["conversation_runs.id"],
            name="fk_independent_chat_jobs_pending_approval",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "parent_conversation_id <> child_conversation_id AND parent_run_id <> child_run_id",
            name="ck_independent_chat_jobs_distinct_execution",
        ),
        sa.UniqueConstraint("parent_run_id", "request_id", name="uq_independent_chat_jobs_request"),
        sa.UniqueConstraint(
            "child_conversation_id", name="uq_independent_chat_jobs_child_conversation"
        ),
        sa.UniqueConstraint("child_run_id", name="uq_independent_chat_jobs_child_run"),
    )
    op.create_index(
        "uq_independent_chat_jobs_source_task",
        _TABLE,
        ["parent_run_id", "source_child_task_id"],
        unique=True,
        sqlite_where=sa.text("source_child_task_id IS NOT NULL"),
        postgresql_where=sa.text("source_child_task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_independent_chat_jobs_parent", _TABLE, ["parent_conversation_id", "created_at"]
    )
    op.create_index(
        "ix_independent_chat_jobs_pending_approval",
        _TABLE,
        ["pending_approval_run_id"],
        sqlite_where=sa.text("pending_approval_run_id IS NOT NULL"),
        postgresql_where=sa.text("pending_approval_run_id IS NOT NULL"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TABLE]):
                op.execute(sa.text(statement))


def downgrade() -> None:
    # Older workers can rehydrate a claimed input or resume approval without
    # checking independent-job authority. Preserve every retained child, including
    # a child whose parent deletion already removed its handoff row.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL app.scope = 'platform'"))
        bind.execute(
            sa.text("LOCK TABLE conversations, independent_chat_jobs IN SHARE ROW EXCLUSIVE MODE")
        )
    retained = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM independent_chat_jobs) "
            "OR EXISTS (SELECT 1 FROM conversations WHERE source = 'independent_job')"
        )
    )
    if retained:
        raise RuntimeError(
            "Cannot downgrade while independent child conversations or handoffs exist. "
            "Keep the current schema and workers to retain their authorization checks."
        )
    op.drop_table(_TABLE)
