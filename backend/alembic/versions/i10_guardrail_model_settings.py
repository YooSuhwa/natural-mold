"""Guardrail model service settings (guardrail-minimum-plan.md §2.1).

``guardrail_model_settings``: at most one platform row (``id = 1``) holding whether the
Korean model pack is on, the service URL and an optional system ``http_bearer``
credential. No row means the environment settings apply. Platform-only under company
RLS (``rls_manifest.PLATFORM_ONLY``). Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i10_guardrail_model_settings"
down_revision = "i09_token_usage_credential_scope"
branch_labels = None
depends_on = None

_TABLE = "guardrail_model_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_guardrail_model_settings_singleton"),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name="fk_guardrail_model_settings_credential_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_guardrail_model_settings_updated_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_model_settings"),
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
