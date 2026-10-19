"""I-06 (plan 8.8, m92): company row-level security policies and app-role grants.

PostgreSQL only (SQLite validates the chain; it has no RLS). Upgrade:

1. Classify every physical table (``rls_manifest``); an unreviewed table aborts
   before any DDL.
2. Install one ``tenant_boundary`` policy per protected table (inert until RLS
   is enabled on the table).
3. When ``MIGRATION_DATABASE_URL`` separates roles: refuse while the
   ``DATABASE_URL`` role still owns tables (prints the REASSIGN command),
   pre-create the checkpointer/APScheduler tables, then grant the app role DML
   only (it never owns or creates tables).
4. Only with ``DB_RLS_ENABLED=true``: ``ENABLE`` + ``FORCE ROW LEVEL SECURITY``
   (requires a ``BYPASSRLS`` migration role). Otherwise activate later with
   ``python -m app.migrations.org_authz_v1.rls_activation enable``.

Runbook: ``docs/operator-setup.md`` section 8. Downgrade disables RLS and drops
the policies; grants and runtime tables are left in place.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_activation import (
    app_role,
    install_policies,
    require_app_role_not_owner,
    set_rls,
)
from app.migrations.org_authz_v1.rls_policies import drop_policy_sql, grant_app_role_sql
from app.migrations.org_authz_v1.rls_runtime_tables import precreate_runtime_tables

revision = "i06_company_rls"
down_revision = "i07_tenant_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role = app_role(bind)
    if role is not None:
        require_app_role_not_owner(bind, role)
        precreate_runtime_tables(bind)
    install_policies(bind)
    if role is not None:
        quote = bind.dialect.identifier_preparer.quote
        for statement in grant_app_role_sql(quote, role):
            op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        set_rls(bind, enabled=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    tables = set_rls(bind, enabled=False)
    for statement in drop_policy_sql(bind.dialect.identifier_preparer.quote, tables):
        op.execute(sa.text(statement))
