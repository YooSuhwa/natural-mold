"""Company policies per tool or MCP server (tool-governance-plan 3.4).

``resource_policies``: one company row (RLS ``DIRECT_TENANT``) per resource key, at most
one company-wide row per key. Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i37_resource_policies"
down_revision = "i36_tool_approval_defaults"
branch_labels = None
depends_on = None

_TABLE = "resource_policies"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("resource_key", sa.String(length=120), nullable=False),
        sa.Column("resource_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("default_mode", sa.String(length=10), nullable=True),
        sa.Column("allowed_modes", sa.JSON(), nullable=True),
        sa.Column("rule", sa.JSON(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{_TABLE}_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=f"fk_{_TABLE}_updated_by_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(
        f"uq_{_TABLE}_company_key",
        _TABLE,
        ["tenant_id", "resource_key"],
        unique=True,
        sqlite_where=sa.text("org_id IS NULL"),
        postgresql_where=sa.text("org_id IS NULL"),
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
    op.drop_index(f"uq_{_TABLE}_company_key", table_name=_TABLE)
    op.drop_table(_TABLE)
