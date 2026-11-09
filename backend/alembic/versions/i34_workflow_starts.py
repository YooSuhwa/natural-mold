"""Scheduled and webhook starts of workflows (workflow-starts-sharing-plan 3.1-3.2).

``workflow_schedules`` and ``workflow_webhooks`` (company rows, RLS ``DIRECT_TENANT``);
``workflow_runs`` gains how it started (``started_from``) and the schedule or webhook.

Downgrade drops the tables and the columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i34_workflow_starts"
down_revision = "i33_workflow_notify_kind"
branch_labels = None
depends_on = None

_TABLES = ("workflow_schedules", "workflow_webhooks")
_STARTS = ("manual", "chat_tool", "schedule", "webhook")


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
        "workflow_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_scope("workflow_schedules"),
        _fk("workflow_schedules", "workflow_id", "workflows", "CASCADE"),
        _fk("workflow_schedules", "user_id", "users", "CASCADE"),
        sa.CheckConstraint(
            _in("kind", ("interval", "cron", "once")), name="ck_workflow_schedules_kind"
        ),
        sa.CheckConstraint(
            _in("status", ("active", "paused")), name="ck_workflow_schedules_status"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_schedules"),
    )
    op.create_index("ix_workflow_schedules_due", "workflow_schedules", ["status", "next_run_at"])
    op.create_index("ix_workflow_schedules_workflow_id", "workflow_schedules", ["workflow_id"])
    op.create_table(
        "workflow_webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_id", sa.String(40), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_hint", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_scope("workflow_webhooks"),
        _fk("workflow_webhooks", "workflow_id", "workflows", "CASCADE"),
        _fk("workflow_webhooks", "user_id", "users", "CASCADE"),
        sa.CheckConstraint(
            _in("status", ("active", "disabled")), name="ck_workflow_webhooks_status"
        ),
        sa.UniqueConstraint("public_id", name="uq_workflow_webhooks_public_id"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_webhooks"),
    )
    op.create_index("ix_workflow_webhooks_workflow_id", "workflow_webhooks", ["workflow_id"])
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(
            sa.Column("started_from", sa.String(20), nullable=False, server_default="manual")
        )
        batch.add_column(sa.Column("schedule_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("webhook_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_workflow_runs_schedule_id_workflow_schedules",
            "workflow_schedules",
            ["schedule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_workflow_runs_webhook_id_workflow_webhooks",
            "workflow_webhooks",
            ["webhook_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint("ck_workflow_runs_started_from", _in("started_from", _STARTS))
        batch.create_index("ix_workflow_runs_webhook_id", ["webhook_id"])
    # Runs a chat tool started before this column existed.
    op.execute(
        sa.text(
            "UPDATE workflow_runs SET started_from = 'chat_tool' "
            "WHERE parent_conversation_id IS NOT NULL"
        )
    )
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
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_index("ix_workflow_runs_webhook_id")
        batch.drop_constraint("ck_workflow_runs_started_from", type_="check")
        batch.drop_constraint("fk_workflow_runs_webhook_id_workflow_webhooks", type_="foreignkey")
        batch.drop_constraint("fk_workflow_runs_schedule_id_workflow_schedules", type_="foreignkey")
        batch.drop_column("webhook_id")
        batch.drop_column("schedule_id")
        batch.drop_column("started_from")
    op.drop_index("ix_workflow_webhooks_workflow_id", table_name="workflow_webhooks")
    op.drop_table("workflow_webhooks")
    op.drop_index("ix_workflow_schedules_workflow_id", table_name="workflow_schedules")
    op.drop_index("ix_workflow_schedules_due", table_name="workflow_schedules")
    op.drop_table("workflow_schedules")
