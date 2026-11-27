"""Store one reserved search-attempt counter per organization and billing month."""

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i42_research_search_usage"
down_revision = "i39_skill_approval_default"
branch_labels = None
depends_on = None

_TABLE = "research_search_usage"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "org_id", "month_start", name=f"pk_{_TABLE}"),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            ondelete="CASCADE",
            name=f"fk_{_TABLE}_org_id_organizations",
        ),
        sa.CheckConstraint("attempts >= 0", name=f"ck_{_TABLE}_attempts"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TABLE]):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table(_TABLE)
