"""Durable goal-scoped sandbox leases and file-reader pins."""

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i45_goal_sandboxes"
down_revision = "i43_chat_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_sandboxes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("goal_version", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("skill_hash", sa.String(64), nullable=False),
        sa.Column("skill_set", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("provider_id", sa.String(255), unique=True),
        sa.Column("operation_token", sa.Uuid()),
        sa.Column("operation_until", sa.DateTime()),
        sa.Column("output_run_id", sa.Uuid()),
        sa.Column("pending_run_id", sa.Uuid()),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("pending_manifest", sa.JSON(none_as_null=True)),
        sa.Column("resolution_reason", sa.String(32)),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime()),
        sa.Column("retry_after", sa.DateTime()),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_goal_sandboxes_org_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "goal_version > 0 AND policy_version > 0", name="ck_goal_sandboxes_versions"
        ),
        sa.CheckConstraint(
            "state IN ('new','executing','ready','pausing','paused','needs_recovery',"
            "'recovering','retiring','deleting','deleted','unresolved')",
            name="ck_goal_sandboxes_state",
        ),
        sa.CheckConstraint(
            "(state IN ('executing','pausing','recovering','deleting') AND "
            "operation_token IS NOT NULL AND operation_until IS NOT NULL) OR "
            "(state NOT IN ('executing','pausing','recovering','deleting') AND "
            "operation_token IS NULL AND operation_until IS NULL)",
            name="ck_goal_sandboxes_operation",
        ),
    )
    op.create_index(
        "uq_goal_sandboxes_live_scope",
        "goal_sandboxes",
        ["scope_hash"],
        unique=True,
        postgresql_where=sa.text("state <> 'deleted'"),
        sqlite_where=sa.text("state <> 'deleted'"),
    )
    op.create_index(
        "ix_goal_sandboxes_maintenance", "goal_sandboxes", ["tenant_id", "state", "updated_at"]
    )
    op.create_index(
        "ix_goal_sandboxes_conversation",
        "goal_sandboxes",
        ["tenant_id", "org_id", "user_id", "conversation_id"],
    )
    op.create_table(
        "goal_sandbox_reads",
        sa.Column("token", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("goal_sandboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_goal_sandbox_reads_expiry", "goal_sandbox_reads", ["workspace_id", "expires_at"]
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        tables = ["goal_sandboxes", "goal_sandbox_reads"]
        for table in tables:
            for statement in create_policy_sql(quote, table_policy(quote, table)):
                op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, tables):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table("goal_sandbox_reads")
    op.drop_table("goal_sandboxes")
