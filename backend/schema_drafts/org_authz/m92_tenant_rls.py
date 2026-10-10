"""Deferred operational draft: activate company RLS before PR2 legacy cleanup.

The original consecutive chain required deleting legacy columns before I-06.
Keep revision IDs but place m92 after m89, with m90/m91 following it, so the
two-week enforcement-stabilization period can retain legacy rollback columns.
"""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.rls_policies import policy_inventory
from schema_drafts.org_authz.rollout_gate import require_rollout_receipt

revision = "m92_tenant_rls"
down_revision = "m89_audit_spend_org"
branch_labels = None
depends_on = None


def create_policy(name: str, read: str, write: str) -> None:
    table = op.get_bind().dialect.identifier_preparer.quote(name)
    op.execute(
        sa.text(f"CREATE POLICY tenant_boundary ON {table} USING ({read}) WITH CHECK ({write})")
    )
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def upgrade() -> None:
    require_rollout_receipt(stabilization=False)
    if op.get_bind().dialect.name != "postgresql":
        # SQLite validates schema/migrations; PostgreSQL is the supported RLS engine.
        return
    for policy in policy_inventory(op.get_bind()):
        create_policy(policy.table, policy.read, policy.write)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    preparer = op.get_bind().dialect.identifier_preparer
    for policy in policy_inventory(op.get_bind()):
        table = preparer.quote(policy.table)
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY tenant_boundary ON {table}"))
