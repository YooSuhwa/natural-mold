"""m92 (I-06): ``tenant_boundary`` policy expressions and the DDL that installs them.

Sessions set ``app.tenant_id``/``app.user_id`` with transaction-local
``set_config(..., true)`` (``app/database.py``); trusted infrastructure sets
``app.scope=platform``, trusted login gateways ``app.scope=identity``. Casts
always go through ``nullif(..., '')`` because a reused connection keeps an
empty string after the transaction ends (plan 8.8 rule 1).

Statement builders are pure (identifier quoting is injected) so unit tests can
check the generated SQL for every table without PostgreSQL.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, assert_never

from app.migrations.org_authz_v1.rls_children import CHILDREN, Parent
from app.migrations.org_authz_v1.rls_manifest import (
    DIRECT_TENANT,
    GLOBAL_IDENTITY,
    GLOBAL_SELF,
    IDENTITY_DIRECTORY,
    PLATFORM_CATALOG,
    PLATFORM_ONLY,
    SpecialTable,
)

POLICY: Final = "tenant_boundary"
TENANT: Final = "nullif(current_setting('app.tenant_id', true), '')::uuid"
USER: Final = "nullif(current_setting('app.user_id', true), '')::uuid"
PLATFORM: Final = "current_setting('app.scope', true) = 'platform'"
IDENTITY: Final = "current_setting('app.scope', true) = 'identity'"

Quote = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class TablePolicy:
    table: str
    read: str
    write: str


def parent_exists(quote: Quote, child: str, parent: Parent, *, catalog: bool = False) -> str:
    """Resolve ownership through an RLS-protected parent, never through FK trust."""
    local = f"{quote(child)}.{quote(parent.local_column)}"
    predicate = f"boundary_parent.id = {local}"
    if parent.table in DIRECT_TENANT and not parent.catalog_reference:
        allowed = f"boundary_parent.tenant_id = {TENANT}"
        if catalog:
            allowed = f"({allowed} OR boundary_parent.tenant_id IS NULL)"
        predicate += f" AND ({allowed})"
    # Identifiers come from the static manifest and are dialect-quoted.
    result = f"EXISTS (SELECT 1 FROM {quote(parent.table)} AS boundary_parent WHERE {predicate})"  # noqa: S608
    return f"({local} IS NULL OR {result})" if parent.optional else result


def _scoped(read: str, write: str) -> tuple[str, str]:
    return (
        f"({PLATFORM}) OR ({TENANT} IS NOT NULL AND ({read}))",
        f"({PLATFORM}) OR ({TENANT} IS NOT NULL AND ({write}))",
    )


def _identity(name: str) -> TablePolicy:
    own = f"{'id' if name == 'users' else 'user_id'} = {USER}"
    if name == "users":  # company members see each other's profile rows
        own += (
            " OR EXISTS (SELECT 1 FROM tenant_members AS member "  # noqa: S608 - fixed SQL
            f"WHERE member.user_id = users.id AND member.tenant_id = {TENANT} "
            "AND member.status = 'active')"
        )
    trusted = f"({PLATFORM}) OR ({IDENTITY})"
    return TablePolicy(name, f"({trusted}) OR ({own})", trusted)


def _skill_usage(quote: Quote, name: str) -> str:
    """Own-company skill, or a platform catalog skill used from a company conversation/agent.

    A catalog skill row is only bound to a company through a non-null
    conversation or agent (each checked against the session company), so a
    platform skill's usage never becomes visible to every company.
    """
    optional = " AND ".join(
        parent_exists(quote, name, parent)
        for parent in (
            Parent("conversations", "conversation_id", optional=True),
            Parent("skill_evaluation_runs", "evaluation_run_id", optional=True),
            Parent("agents", "agent_id", optional=True),
        )
    )
    own = parent_exists(quote, name, Parent("skills", "skill_id"))
    catalog = (
        "EXISTS (SELECT 1 FROM skills AS boundary_parent WHERE boundary_parent.id = "  # noqa: S608
        f"{quote(name)}.skill_id AND boundary_parent.tenant_id IS NULL) AND "
        "(conversation_id IS NOT NULL OR agent_id IS NOT NULL)"
    )
    return f"({PLATFORM}) OR ({TENANT} IS NOT NULL AND (({own}) OR ({catalog})) AND {optional})"


def _special(quote: Quote, name: str) -> TablePolicy:
    match SpecialTable(name):
        case SpecialTable.TENANTS:
            read, write = _scoped(f"id = {TENANT}", f"id = {TENANT}")
        case SpecialTable.TEMPLATES:  # 8.9: platform-only agent templates, read by all
            read, write = f"({PLATFORM}) OR ({TENANT} IS NOT NULL)", PLATFORM
        case SpecialTable.HEALTH_HISTORY:
            kinds = (("model", "models"), ("mcp_server", "mcp_servers"))

            def allowed(*, catalog: bool) -> str:
                return " OR ".join(
                    f"(target_kind = '{kind}' AND "
                    f"{parent_exists(quote, name, Parent(table, 'target_id'), catalog=catalog)})"
                    for kind, table in kinds
                )

            # Companies read the health of platform catalog models/servers; only
            # the platform health sweep writes it.
            read, write = _scoped(allowed(catalog=True), allowed(catalog=False))
        case SpecialTable.SKILL_USAGE:
            read = write = _skill_usage(quote, name)
        case unreachable:
            assert_never(unreachable)
    return TablePolicy(name, read, write)


def table_policy(quote: Quote, name: str) -> TablePolicy:
    policy = _table_policy(quote, name)
    if name not in IDENTITY_DIRECTORY:
        return policy
    gateway = f"({IDENTITY}) OR "
    return TablePolicy(name, gateway + f"({policy.read})", gateway + f"({policy.write})")


def _table_policy(quote: Quote, name: str) -> TablePolicy:
    if name in DIRECT_TENANT:
        write = f"tenant_id = {TENANT}"
        read = f"({write} OR tenant_id IS NULL)" if name in PLATFORM_CATALOG else write
        return TablePolicy(name, *_scoped(read, write))
    if name in PLATFORM_ONLY:
        return TablePolicy(name, PLATFORM, PLATFORM)
    if name in GLOBAL_IDENTITY:
        return _identity(name)
    if name in GLOBAL_SELF:
        own = f"({PLATFORM}) OR (user_id = {USER})"
        return TablePolicy(name, own, own)
    if name in CHILDREN:
        rule = CHILDREN[name]
        read = " AND ".join(
            parent_exists(quote, name, parent, catalog=rule.catalog_read) for parent in rule.parents
        )
        write = " AND ".join(parent_exists(quote, name, parent) for parent in rule.parents)
        return TablePolicy(name, *_scoped(read, write))
    return _special(quote, name)


def create_policy_sql(quote: Quote, policy: TablePolicy) -> tuple[str, ...]:
    table = quote(policy.table)
    return (
        f"DROP POLICY IF EXISTS {POLICY} ON {table}",
        f"CREATE POLICY {POLICY} ON {table} AS PERMISSIVE FOR ALL "
        f"USING ({policy.read}) WITH CHECK ({policy.write})",
    )


def enable_sql(quote: Quote, tables: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        statement
        for name in tables
        for statement in (
            f"ALTER TABLE {quote(name)} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {quote(name)} FORCE ROW LEVEL SECURITY",
        )
    )


def disable_sql(quote: Quote, tables: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        statement
        for name in tables
        for statement in (
            f"ALTER TABLE {quote(name)} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {quote(name)} DISABLE ROW LEVEL SECURITY",
        )
    )


def drop_policy_sql(quote: Quote, tables: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"DROP POLICY IF EXISTS {POLICY} ON {quote(name)}" for name in tables)


def grant_app_role_sql(quote: Quote, role: str) -> tuple[str, ...]:
    """DML only: the app role never owns, alters or creates Moldy tables."""
    target = quote(role)
    return (
        f"GRANT USAGE ON SCHEMA public TO {target}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {target}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {target}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {target}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {target}",
    )
