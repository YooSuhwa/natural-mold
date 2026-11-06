"""Company guardrail templates, published versions and agent bindings (plan §3.4).

``guardrail_templates`` (drafts), ``guardrail_template_versions`` (published, immutable)
and ``agent_guardrail_bindings`` (inherit / template / off + rule overrides). Company rows
(RLS ``DIRECT_TENANT``). The company policy itself lives in ``tenants.settings``.
Downgrade drops the three tables.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i13_guardrail_templates"
down_revision = "i12_guardrail_pseudonyms"
branch_labels = None
depends_on = None

_TABLES = ("guardrail_templates", "guardrail_template_versions", "agent_guardrail_bindings")


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _tenant(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant_id_tenants", ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "guardrail_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("draft_rules", sa.JSON(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _tenant("guardrail_templates"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_guardrail_templates_created_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')", name="ck_guardrail_templates_status"
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_guardrail_templates_name"),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_templates"),
    )
    op.create_table(
        "guardrail_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        _now(),
        _tenant("guardrail_template_versions"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["guardrail_templates.id"],
            name="fk_guardrail_template_versions_template_id_guardrail_templates",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by"],
            ["users.id"],
            name="fk_guardrail_template_versions_published_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "template_id", "version", name="uq_guardrail_template_versions_version"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_template_versions"),
    )
    op.create_table(
        "agent_guardrail_bindings",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="inherit"),
        sa.Column("template_ref", sa.String(length=80), nullable=True),
        sa.Column("rule_overrides", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_guardrail_bindings_agent_id_agents",
            ondelete="CASCADE",
        ),
        _tenant("agent_guardrail_bindings"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_agent_guardrail_bindings_updated_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "mode IN ('inherit','template','off')", name="ck_agent_guardrail_bindings_mode"
        ),
        sa.PrimaryKeyConstraint("agent_id", name="pk_agent_guardrail_bindings"),
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
    for table in reversed(_TABLES):
        op.drop_table(table)
