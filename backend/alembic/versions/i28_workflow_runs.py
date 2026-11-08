"""Workflow runs and steps (feature plan v2 순번 23, workflow-plan 3.4, W2a).

* ``workflow_runs`` (company table, RLS ``DIRECT_TENANT``): one run of a published version
  by its runner: status, input, output, failure, cancel request.
* ``workflow_step_runs`` (company table, RLS ``DIRECT_TENANT``): one row per node of a
  run, in run order. An agent node's row is the execution **envelope**: its accepted
  ``conversation_runs`` row binds here once (``conversation_run_id``, unique).

Downgrade drops both tables (run history is lost).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i28_workflow_runs"
down_revision = "i27_workflows"
branch_labels = None
depends_on = None

_TABLES = ("workflow_runs", "workflow_step_runs")
_RUN_STATUSES = (
    "queued",
    "running",
    "waiting",
    "unknown",
    "completed",
    "failed",
    "canceling",
    "canceled",
)
_STEP_STATUSES = ("queued", "running", "waiting", "completed", "failed", "unknown", "canceled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _scope(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{table}_org_tenant",
            ondelete="RESTRICT",
        ),
    ]


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("current_node", sa.String(64), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_scope("workflow_runs"),
        _fk("workflow_runs", "workflow_id", "workflows", "CASCADE"),
        _fk("workflow_runs", "version_id", "workflow_versions", "CASCADE"),
        _fk("workflow_runs", "user_id", "users", "CASCADE"),
        sa.CheckConstraint(_in("status", _RUN_STATUSES), name="ck_workflow_runs_status"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
    )
    op.create_index(
        "ix_workflow_runs_workflow_created", "workflow_runs", ["workflow_id", "created_at"]
    )
    op.create_table(
        "workflow_step_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("branch", sa.String(20), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_run_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_scope("workflow_step_runs"),
        _fk("workflow_step_runs", "run_id", "workflow_runs", "CASCADE"),
        _fk("workflow_step_runs", "user_id", "users", "CASCADE"),
        _fk("workflow_step_runs", "agent_id", "agents", "SET NULL"),
        _fk("workflow_step_runs", "conversation_id", "conversations", "SET NULL"),
        _fk("workflow_step_runs", "conversation_run_id", "conversation_runs", "SET NULL"),
        sa.CheckConstraint(_in("status", _STEP_STATUSES), name="ck_workflow_step_runs_status"),
        sa.UniqueConstraint("run_id", "node_id", name="uq_workflow_step_runs_node"),
        sa.UniqueConstraint("conversation_run_id", name="uq_workflow_step_runs_conversation_run"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_step_runs"),
    )
    op.create_index("ix_workflow_step_runs_run_id", "workflow_step_runs", ["run_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for table in _TABLES:
        for statement in create_policy_sql(quote, table_policy(quote, table)):
            op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, list(_TABLES)):
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_index("ix_workflow_step_runs_run_id", table_name="workflow_step_runs")
    op.drop_table("workflow_step_runs")
    op.drop_index("ix_workflow_runs_workflow_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
