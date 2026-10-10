"""Policy expressions for a trusted app role with transaction-local context.

The role must have NOBYPASSRLS. App SQL must set app.tenant_id/app.user_id
locally; only trusted login/account gateways may set app.scope=identity, and
only trusted infrastructure/migration workers may set app.scope=platform.
These are internal service contexts, never HTTP input or is_super_user roles.
Platform operators do not gain tenant-content access from their product role.
Neither a public share link nor an API key implicitly grants privileged scope.
These are deferred migration contracts, not an enabled application auth switch.
LangGraph's separate autocommit saver pool must use a trusted checkpoint-service
role/context only behind conversation_checkpointer(thread_id, ctx), after its
conversation tenant/ownership check. Tenant HTTP sessions never receive this
scope. Saver setup must precede m92; new runtime-managed tables require review.
"""

from dataclasses import dataclass
from typing import Final, assert_never

from sqlalchemy.engine import Connection

from schema_drafts.org_authz.rls_manifest import (
    CHILDREN,
    DIRECT_TENANT,
    GLOBAL_IDENTITY,
    GLOBAL_SELF,
    PLATFORM_CATALOG,
    PLATFORM_ONLY,
    Parent,
    SpecialTable,
    classified_tables,
)

TENANT: Final = "nullif(current_setting('app.tenant_id', true), '')::uuid"
USER: Final = "nullif(current_setting('app.user_id', true), '')::uuid"
PLATFORM: Final = "current_setting('app.scope', true) = 'platform'"
IDENTITY: Final = "current_setting('app.scope', true) = 'identity'"


@dataclass(frozen=True, slots=True)
class TablePolicy:
    table: str
    read: str
    write: str


def parent_exists(
    connection: Connection, child: str, parent: Parent, *, catalog: bool = False
) -> str:
    """Resolve ownership through an RLS-protected parent, never through FK trust."""
    quote = connection.dialect.identifier_preparer.quote
    local = f"{quote(child)}.{quote(parent.local_column)}"
    predicate = f"boundary_parent.id = {local}"
    if parent.table in DIRECT_TENANT and not parent.catalog_reference:
        allowed = f"boundary_parent.tenant_id = {TENANT}"
        if catalog:
            allowed = f"({allowed} OR boundary_parent.tenant_id IS NULL)"
        predicate += f" AND ({allowed})"
    # All identifiers come from the static manifest and are dialect-quoted.
    result = f"EXISTS (SELECT 1 FROM {quote(parent.table)} AS boundary_parent WHERE {predicate})"  # noqa: S608
    if parent.optional:
        result = f"({local} IS NULL OR {result})"
    return result


def scoped(read: str, write: str) -> tuple[str, str]:
    return (
        f"({PLATFORM}) OR ({TENANT} IS NOT NULL AND ({read}))",
        f"({PLATFORM}) OR ({TENANT} IS NOT NULL AND ({write}))",
    )


def table_policy(connection: Connection, name: str) -> TablePolicy:
    if name in DIRECT_TENANT:
        write = f"tenant_id = {TENANT}"
        read = f"({write} OR tenant_id IS NULL)" if name in PLATFORM_CATALOG else write
        read, write = scoped(read, write)
        return TablePolicy(name, read, write)
    if name in PLATFORM_ONLY:
        return TablePolicy(name, PLATFORM, PLATFORM)
    if name in GLOBAL_IDENTITY:
        own = f"{'id' if name == 'users' else 'user_id'} = {USER}"
        projection = own
        if name == "users":
            projection += (
                " OR EXISTS (SELECT 1 FROM tenant_members AS member "  # noqa: S608 - fixed TENANT SQL constant
                f"WHERE member.user_id = users.id AND member.tenant_id = {TENANT} "
                "AND member.status = 'active')"
            )
        trusted = f"({PLATFORM}) OR ({IDENTITY})"
        return TablePolicy(name, f"({trusted}) OR ({projection})", trusted)
    if name in GLOBAL_SELF:
        own = f"user_id = {USER}"
        return TablePolicy(name, f"({PLATFORM}) OR ({own})", f"({PLATFORM}) OR ({own})")
    if name in CHILDREN:
        rule = CHILDREN[name]
        read = " AND ".join(
            parent_exists(connection, name, parent, catalog=rule.catalog_read)
            for parent in rule.parents
        )
        write = " AND ".join(parent_exists(connection, name, parent) for parent in rule.parents)
        read, write = scoped(read, write)
        return TablePolicy(name, read, write)
    match SpecialTable(name):
        case SpecialTable.TENANTS:
            read, write = scoped(f"id = {TENANT}", f"id = {TENANT}")
        case SpecialTable.TEMPLATES:
            read, write = f"({PLATFORM}) OR ({TENANT} IS NOT NULL)", PLATFORM
        case SpecialTable.HEALTH_HISTORY:
            parents = (
                ("model", Parent("models", "target_id")),
                ("mcp_server", Parent("mcp_servers", "target_id")),
            )
            allowed = " OR ".join(
                f"(target_kind = '{kind}' AND {parent_exists(connection, name, parent)})"
                for kind, parent in parents
            )
            read, write = scoped(allowed, allowed)
        case SpecialTable.SKILL_USAGE:
            # A nullable conversation/evaluation FK can outlive its parent; the
            # non-null skill supplies the safe retained-history fallback. Global
            # catalog skill usage remains platform-only rather than leaking users.
            parents = (
                Parent("skills", "skill_id"),
                Parent("conversations", "conversation_id", optional=True),
                Parent("skill_evaluation_runs", "evaluation_run_id", optional=True),
                Parent("agents", "agent_id", optional=True),
            )
            allowed = " AND ".join(parent_exists(connection, name, parent) for parent in parents)
            read, write = scoped(allowed, allowed)
        case unreachable:
            assert_never(unreachable)
    return TablePolicy(name, read, write)


def policy_inventory(connection: Connection) -> tuple[TablePolicy, ...]:
    return tuple(table_policy(connection, name) for name in classified_tables(connection))
