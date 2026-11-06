"""Conversation pseudonym maps for the guardrail (guardrail-minimum-plan.md §3.3.1).

``guardrail_pseudonyms``: token -> Cipher V2 encrypted value per conversation, with a
company-keyed digest for lookups. Company rows (RLS ``DIRECT_TENANT``) cascading with the
company and the conversation. Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i12_guardrail_pseudonyms"
down_revision = "i11_guardrail_events"
branch_labels = None
depends_on = None

_TABLE = "guardrail_pseudonyms"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=40), nullable=False),
        sa.Column("value_digest", sa.String(length=64), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_guardrail_pseudonyms_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_guardrail_pseudonyms_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_pseudonyms"),
        sa.UniqueConstraint("conversation_id", "token", name="uq_guardrail_pseudonyms_token"),
        sa.UniqueConstraint(
            "conversation_id", "value_digest", name="uq_guardrail_pseudonyms_value"
        ),
    )
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
