"""I-06 follow-up: request scopes may enqueue ``authz_outbox`` rows under RLS.

Signup (identity scope) and company sessions write the tuples of their own membership
and share changes into the outbox inside the same transaction. The i06 policy made the
queue platform-only, so with ``DB_RLS_ENABLED=true`` every signup failed with
"new row violates row-level security policy for table authz_outbox". Reading, updating
and deleting queue rows stays platform-only (the drain). PostgreSQL only.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.migrations.org_authz_v1.rls_policies import (
    AUTHZ_QUEUE,
    PLATFORM,
    TablePolicy,
    create_policy_sql,
    table_policy,
)

revision = "i08_outbox_request_enqueue"
down_revision = "i06_company_rls"
branch_labels = None
depends_on = None


def _install(policy: TablePolicy) -> None:
    for statement in create_policy_sql(op.get_bind().dialect.identifier_preparer.quote, policy):
        op.execute(sa.text(statement))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _install(table_policy(op.get_bind().dialect.identifier_preparer.quote, AUTHZ_QUEUE))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _install(TablePolicy(AUTHZ_QUEUE, PLATFORM, PLATFORM))
