"""Guardrail detection records (guardrail-minimum-plan.md §3.5).

``guardrail_events``: one row per check, stage, rule, entity and action, with a count and
the company-keyed value digests; never text or values. Company rows (RLS ``DIRECT_TENANT``),
cascading with the company and the conversation. Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i11_guardrail_events"
down_revision = "i10_guardrail_model_settings"
branch_labels = None
depends_on = None

_TABLE = "guardrail_events"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("value_digests", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_guardrail_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_guardrail_events_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_guardrail_events_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_events"),
    )
    op.create_index("ix_guardrail_events_tenant_created", _TABLE, ["tenant_id", "created_at"])
    op.create_index("ix_guardrail_events_conversation", _TABLE, ["conversation_id"])
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
        op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, [_TABLE]):
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table(_TABLE)
