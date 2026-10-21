"""I-06 ratchet: m92 classifies every table and generates a forced policy for each.

The table list is derived from the ORM metadata (what Alembic builds), so a new
model without a reviewed RLS classification fails here before the migration
would abort on a real database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401 - register every model on the metadata
from app.database import Base
from app.migrations.org_authz_v1.rls_children import CHILDREN
from app.migrations.org_authz_v1.rls_manifest import (
    CLASSIFIED,
    DIRECT_TENANT,
    PLATFORM_CATALOG,
    PLATFORM_ONLY,
    RUNTIME_EXEMPT,
    inventory_problems,
    protected_tables,
)
from app.migrations.org_authz_v1.rls_policies import (
    TENANT,
    create_policy_sql,
    enable_sql,
    grant_app_role_sql,
    table_policy,
)

TABLES = Base.metadata.tables
COLUMNS = {name: [column.name for column in table.c] for name, table in TABLES.items()}
QUOTE = postgresql.dialect().identifier_preparer.quote
# Physical tables that exist outside the ORM metadata (runtime stores, history).
EXTERNAL = RUNTIME_EXEMPT | {
    "_m11_dedup_connection_snapshot",
    "_m11_dedup_tool_remap",
    "_m11_tool_backfill_provenance",
    "_m9_migrated_connections",
    "agent_creation_sessions",
}


def _all_sql() -> dict[str, str]:
    return {
        name: " ; ".join(create_policy_sql(QUOTE, table_policy(QUOTE, name)))
        for name in protected_tables(TABLES)
    }


def test_every_metadata_table_is_classified_with_a_valid_shape() -> None:
    assert inventory_problems(TABLES, COLUMNS) == []


def test_classification_has_no_stale_names() -> None:
    assert CLASSIFIED - set(TABLES) == EXTERNAL


def test_every_tenant_id_table_uses_the_direct_company_policy() -> None:
    with_tenant = {name for name, table in TABLES.items() if "tenant_id" in table.c}
    assert with_tenant == DIRECT_TENANT
    nullable = {name for name in with_tenant if TABLES[name].c.tenant_id.nullable}
    # Platform rows (tenant_id NULL) are readable catalog data only where reviewed.
    assert nullable >= PLATFORM_CATALOG


def test_every_protected_table_gets_policy_enable_and_force() -> None:
    protected = protected_tables(TABLES)
    assert set(protected) == set(TABLES) - RUNTIME_EXEMPT
    statements = enable_sql(QUOTE, protected)
    for name in protected:
        table = QUOTE(name)
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in statements
    sql = _all_sql()
    assert set(sql) == set(protected)
    assert all("CREATE POLICY tenant_boundary ON" in text for text in sql.values())


def test_policies_cast_through_nullif_and_never_set_settings() -> None:
    bare_cast = re.compile(r"(?<!nullif\()current_setting\('app\.(tenant|user)_id', true\)::")
    for name, text in _all_sql().items():
        assert not bare_cast.search(text), name
        assert "set_config" not in text and "SET app." not in text, name


def test_direct_and_child_policies_bind_writes_to_the_session_company() -> None:
    agents = table_policy(QUOTE, "agents")
    assert f"tenant_id = {TENANT}" in agents.write and "IS NULL" not in agents.write
    tools = table_policy(QUOTE, "tools")
    assert "tenant_id IS NULL" in tools.read and "tenant_id IS NULL" not in tools.write
    events = table_policy(QUOTE, "message_events")
    assert f"{QUOTE('conversations')} AS boundary_parent" in events.write
    assert f"boundary_parent.tenant_id = {TENANT}" in events.write
    for name in PLATFORM_ONLY & set(TABLES):
        assert table_policy(QUOTE, name).read == "current_setting('app.scope', true) = 'platform'"


def test_request_scopes_only_enqueue_into_the_authz_outbox() -> None:
    # Signup (identity) and company sessions enqueue their own tuple changes; only the
    # platform drain reads (and so updates or deletes) queue rows.
    queue = table_policy(QUOTE, "authz_outbox")
    assert queue.read == "current_setting('app.scope', true) = 'platform'"
    assert "current_setting('app.scope', true) = 'identity'" in queue.write
    assert f"{TENANT} IS NOT NULL" in queue.write
    for name in PLATFORM_ONLY - {"authz_outbox"}:
        assert table_policy(QUOTE, name).write == queue.read


@pytest.mark.parametrize("name", sorted(CHILDREN))
def test_child_policy_checks_every_parent(name: str) -> None:
    policy = table_policy(QUOTE, name)
    for parent in CHILDREN[name].parents:
        assert f"{QUOTE(name)}.{QUOTE(parent.local_column)}" in policy.write


def test_unreviewed_or_misshaped_tables_abort_before_ddl() -> None:
    names = [*TABLES, "unclassified_payload"]
    columns = {**COLUMNS, "agents": [c for c in COLUMNS["agents"] if c != "tenant_id"]}
    problems = inventory_problems(names, columns)
    assert "unclassified_payload" in problems and "agents.tenant_id" in problems


def test_app_role_receives_dml_grants_only() -> None:
    statements = " ".join(grant_app_role_sql(QUOTE, "moldy_app"))
    assert "SELECT, INSERT, UPDATE, DELETE" in statements
    assert not re.search(r"\b(ALL PRIVILEGES|TRUNCATE|CREATE|OWNER|BYPASSRLS)\b", statements)


def test_m92_revision_is_the_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    script = ScriptDirectory.from_config(config)
    (head,) = script.get_heads()
    revisions = {revision.revision for revision in script.iterate_revisions(head, "base")}
    assert "i06_company_rls" in revisions
    assert script.get_revision("i06_company_rls").down_revision == "i07_tenant_exports"


def test_policy_joins_compare_matching_uuid_columns() -> None:
    dialect = postgresql.dialect()
    pairs = [(child, parent) for child, rule in CHILDREN.items() for parent in rule.parents]
    for child, parent in pairs:
        local = TABLES[child].c[parent.local_column].type.compile(dialect)
        assert local == TABLES[parent.table].c.id.type.compile(dialect), (child, parent)
    for name in DIRECT_TENANT:
        assert TABLES[name].c.tenant_id.type.compile(dialect) == "UUID", name


IDENTITY = "current_setting('app.scope', true) = 'identity'"


def test_identity_gateway_reaches_the_account_directory_only() -> None:
    from app.migrations.org_authz_v1.rls_manifest import GLOBAL_IDENTITY, IDENTITY_DIRECTORY

    opened = {
        name for name in protected_tables(TABLES) if IDENTITY in table_policy(QUOTE, name).read
    }
    assert opened == IDENTITY_DIRECTORY | GLOBAL_IDENTITY
    for name in IDENTITY_DIRECTORY | GLOBAL_IDENTITY:
        assert IDENTITY in table_policy(QUOTE, name).write, name
    assert not opened & {"conversations", "agents", "credentials", "memory_records", "skills"}


def test_platform_catalog_parents_where_companies_use_them() -> None:
    usage = table_policy(QUOTE, "skill_usage_events")
    assert "boundary_parent.tenant_id IS NULL" in usage.write
    assert "conversation_id IS NOT NULL OR agent_id IS NOT NULL" in usage.write
    health = table_policy(QUOTE, "health_check_history")
    assert "boundary_parent.tenant_id IS NULL" in health.read
    assert "boundary_parent.tenant_id IS NULL" not in health.write
