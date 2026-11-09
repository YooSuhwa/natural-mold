"""Workflows as chat tools (workflow-tool-plan 3.1-3.4).

``agent_workflows`` links an agent to a workflow it may call as a tool in chat (delegation
columns as the other agent link tables; RLS child of ``agents`` and ``workflows``).
``workflow_runs`` gains the chat that started it: ``parent_conversation_id`` +
``parent_tool_call_id`` (unique together: a resumed tool call
finds its run again), ``parent_conversation_run_id`` and ``chat_resumed_at`` (set once the
chat was resumed with the result).

Downgrade drops the link table and the columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i31_workflow_chat_tools"
down_revision = "i30_workflow_step_rounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_workflows",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("broken_reason", sa.String(64), nullable=True),
        sa.Column("delegated_by", sa.Uuid(), nullable=True),
        sa.Column("link_status", sa.String(20), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_workflows_agent_id_agents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_agent_workflows_workflow_id_workflows",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["delegated_by"],
            ["users.id"],
            name="fk_agent_workflows_delegated_by",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "link_status IN ('active','broken')", name="ck_agent_workflows_link_status"
        ),
        sa.PrimaryKeyConstraint("agent_id", "workflow_id", name="pk_agent_workflows"),
    )
    op.create_index("ix_agent_workflows_workflow_id", "agent_workflows", ["workflow_id"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, "agent_workflows")):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, ["agent_workflows"]):
                op.execute(sa.text(statement))
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("parent_conversation_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("parent_tool_call_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("parent_conversation_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("chat_resumed_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_workflow_runs_parent_conversation_id_conversations",
            "conversations",
            ["parent_conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_workflow_runs_parent_conversation_run_id_conversation_runs",
            "conversation_runs",
            ["parent_conversation_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_workflow_runs_parent_tool_call", ["parent_conversation_id", "parent_tool_call_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_constraint("uq_workflow_runs_parent_tool_call", type_="unique")
        batch.drop_constraint(
            "fk_workflow_runs_parent_conversation_run_id_conversation_runs", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_workflow_runs_parent_conversation_id_conversations", type_="foreignkey"
        )
        batch.drop_column("chat_resumed_at")
        batch.drop_column("parent_conversation_run_id")
        batch.drop_column("parent_tool_call_id")
        batch.drop_column("parent_conversation_id")
    op.drop_index("ix_agent_workflows_workflow_id", table_name="agent_workflows")
    op.drop_table("agent_workflows")
