"""Workflows (feature plan v2 순번 23, workflow-plan 3.1, W1).

* ``workflows`` (company table, RLS ``DIRECT_TENANT``): a person's workflow in one
  organization: draft definition, draft revision, the published version number, archive.
* ``workflow_versions`` (company table, RLS ``DIRECT_TENANT``): immutable published
  definitions with their hash and the agent pins (Q1).

Runs and steps arrive with the runner (W2). Downgrade drops both tables (workflow data
is lost).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i27_workflows"
down_revision = "i26_workflow_run_source"
branch_labels = None
depends_on = None

_TABLES = ("workflows", "workflow_versions")


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


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("draft_definition", sa.JSON(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("published_version_no", sa.Integer(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_scope("workflows"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_workflows_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
    )
    op.create_index("ix_workflows_owner", "workflows", ["user_id", "org_id", "updated_at"])
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("agent_pins", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        *_scope("workflow_versions"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_workflow_versions_workflow_id_workflows",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by"],
            ["users.id"],
            name="fk_workflow_versions_published_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_versions"),
        sa.UniqueConstraint("workflow_id", "version_no", name="uq_workflow_versions_version_no"),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])

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
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index("ix_workflows_owner", table_name="workflows")
    op.drop_table("workflows")
