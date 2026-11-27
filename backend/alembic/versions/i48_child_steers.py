"""Track scoped child invocations and idempotent instructions within a parent run."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i48_child_steers"
down_revision = "i47_chat_notifications"
branch_labels = None
depends_on = None

_TASKS = "conversation_child_tasks"
_RECEIPTS = "conversation_child_instructions"


def upgrade() -> None:
    op.add_column("conversation_run_agents", sa.Column("identity_mode_at_accept", sa.String(16)))
    op.create_table(
        _TASKS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "current_run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(256), nullable=False),
        sa.Column(
            "child_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(160), nullable=False),
        sa.Column("brief", sa.String(300)),
        sa.Column("checkpoint_ns", sa.String(1024), nullable=False),
        sa.Column("checkpoint_id", sa.String(128)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_child_tasks_run_call"),
        sa.CheckConstraint(
            "status IN ('running','awaiting_approval','completed','failed','cancelled')",
            name="ck_child_tasks_status",
        ),
    )
    op.create_index("ix_child_tasks_current_run", _TASKS, ["current_run_id"])
    op.create_table(
        _RECEIPTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "child_id", sa.Uuid(), sa.ForeignKey(f"{_TASKS}.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("client_request_id", sa.String(200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivery_started_at", sa.DateTime()),
        sa.Column("applied_at", sa.DateTime()),
        sa.UniqueConstraint("child_id", "client_request_id", name="uq_child_instructions_request"),
        sa.UniqueConstraint("child_id", "sequence", name="uq_child_instructions_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_child_instructions_sequence"),
        sa.CheckConstraint(
            "status IN ('pending','delivering','applied','cancelled','review_required')",
            name="ck_child_instructions_status",
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for name in (_TASKS, _RECEIPTS):
            for statement in create_policy_sql(quote, table_policy(quote, name)):
                op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TASKS, _RECEIPTS]):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table(_RECEIPTS)
    op.drop_table(_TASKS)
    op.drop_column("conversation_run_agents", "identity_mode_at_accept")
