"""Deferred operational draft: activate company RLS before PR2 legacy cleanup.

The original consecutive chain required deleting legacy columns before I-06.
Keep revision IDs but place m92 after m89, with m90/m91 following it, so the
two-week enforcement-stabilization period can retain legacy rollback columns.
"""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.rollout_gate import require_rollout_receipt

revision = "m92_tenant_rls"
down_revision = "m89_audit_spend_org"
branch_labels = None
depends_on = None
PLATFORM_CATALOG = frozenset(
    {
        "models",
        "system_llm_settings",
        "tools",
        "skills",
        "mcp_servers",
        "credentials",
        "marketplace_items",
    }
)
PLATFORM_ONLY = frozenset(
    {
        "authz_outbox",
        "authz_model_versions",
        "authz_shadow_diffs",
        "_m11_dedup_connection_snapshot",
        "_m11_dedup_tool_remap",
        "_m11_tool_backfill_provenance",
        "_m9_migrated_connections",
        "agent_creation_sessions",
    }
)


def tenant_tables() -> tuple[str, ...]:
    inspector = sa.inspect(op.get_bind())
    return tuple(
        name
        for name in inspector.get_table_names()
        if any(column["name"] == "tenant_id" for column in inspector.get_columns(name))
    )


def platform_tables() -> tuple[str, ...]:
    return tuple(sorted(set(sa.inspect(op.get_bind()).get_table_names()) & PLATFORM_ONLY))


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
    for name in tenant_tables():
        context = "nullif(current_setting('app.tenant_id', true), '')::uuid"
        scope = "current_setting('app.scope', true) = 'platform'"
        allowed = f"tenant_id = {context}"
        if name in PLATFORM_CATALOG:
            allowed = f"({allowed} OR tenant_id IS NULL)"
        predicate = f"({scope}) OR ({context} IS NOT NULL AND ({allowed}))"
        write_predicate = f"({scope}) OR ({context} IS NOT NULL AND tenant_id = {context})"
        create_policy(name, predicate, write_predicate)
    for name in platform_tables():
        scope = "current_setting('app.scope', true) = 'platform'"
        create_policy(name, scope, scope)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    preparer = op.get_bind().dialect.identifier_preparer
    for name in (*tenant_tables(), *platform_tables()):
        table = preparer.quote(name)
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY tenant_boundary ON {table}"))
