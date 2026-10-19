"""m92 (I-06): reviewed physical-table inventory for company row-level security.

Every physical table must be classified before m92 installs a policy, so a new
table without a reviewed company boundary aborts the migration instead of
silently staying readable across companies (plan 8.8, D24).

* ``DIRECT_TENANT``: the row carries ``tenant_id`` (``PLATFORM_CATALOG`` rows
  may have ``tenant_id IS NULL`` = platform catalog, readable by every company).
* ``CHILDREN`` (``rls_children``): the company comes from protected parents.
* ``GLOBAL_IDENTITY`` / ``GLOBAL_SELF``: cross-company identity rows.
* ``PLATFORM_ONLY``: instance infrastructure, platform scope only.
* ``RUNTIME_EXEMPT``: tables written by separate connections outside Moldy's
  scoped sessions (LangGraph checkpointer, APScheduler job store, Alembic).
  Plan 8.8: the checkpointer pool is outside RLS; its tenant boundary is the
  conversation gate plus the checkpoint metadata cross-check.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Final

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.migrations.org_authz_v1.rls_children import CHILDREN

DIRECT_TENANT: Final = frozenset(
    {
        "access_requests",
        "agent_api_keys",
        "agent_api_runs",  # I-07 tenant_id: runs outlive their deployment
        "agent_blueprints",
        "agent_deployments",
        "agent_purge_receipts",
        "agent_trigger_runs",  # I-07 tenant_id: runs outlive their trigger
        "agent_triggers",
        "agents",
        "audit_events",
        "authz_revocations",
        "builder_sessions",
        "conversation_artifacts",
        "conversation_runs",
        "conversations",
        "credential_defaults",
        "credentials",
        "daily_spend_agent",
        "daily_spend_model",
        "daily_spend_user",
        "group_members",
        "groups",
        "marketplace_installations",
        "marketplace_items",
        "mcp_servers",
        "memory_proposals",
        "memory_records",
        "message_attachments",
        "models",
        "notifications",
        "org_capability_grants",
        "org_invitations",
        "organization_members",
        "organization_role_grants",
        "organizations",
        "resource_credential_bindings",
        "resource_grants",
        "share_links",
        "skill_builder_sessions",
        "skills",
        "sso_connections",
        "system_llm_settings",
        "tenant_domains",
        "tenant_exports",  # I-07 operator export jobs (platform sessions)
        "tenant_invitations",
        "tenant_members",
        "tenant_purges",  # I-07 receipts that outlive the company (platform reads)
        "tenant_role_grants",
        "token_usages",
        "tools",
    }
)
PLATFORM_CATALOG: Final = frozenset(
    {
        "credentials",
        "marketplace_items",
        "mcp_servers",
        "models",
        "skills",
        "system_llm_settings",
        "tools",
    }
)
PLATFORM_ONLY: Final = frozenset(
    {
        "authz_model_versions",
        "authz_outbox",
        "authz_reconcile_runs",
        "authz_shadow_diffs",
        # Historical migration artefacts that may still exist physically.
        "_m11_dedup_connection_snapshot",
        "_m11_dedup_tool_remap",
        "_m11_tool_backfill_provenance",
        "_m9_migrated_connections",
        "agent_creation_sessions",
    }
)
GLOBAL_IDENTITY: Final = frozenset({"refresh_tokens", "user_identities", "users"})
# The account directory the identity gateway (``app.scope=identity``: login,
# registration, refresh, SSO sign-in, org-context resolution) reads and writes
# before a company is bound. No conversation/agent/credential payload is here.
IDENTITY_DIRECTORY: Final = frozenset(
    {
        "audit_events",
        "group_members",
        "groups",
        "org_invitations",
        "organization_members",
        "organization_role_grants",
        "organizations",
        "sso_connections",
        "tenant_domains",
        "tenant_invitations",
        "tenant_members",
        "tenant_role_grants",
        "tenants",
    }
)
GLOBAL_SELF: Final = frozenset({"user_memory_settings"})
RUNTIME_EXEMPT: Final = frozenset(
    {
        "alembic_version",
        "apscheduler_jobs",
        "checkpoint_blobs",
        "checkpoint_migrations",
        "checkpoint_writes",
        "checkpoints",
    }
)


class SpecialTable(StrEnum):
    TENANTS = "tenants"
    TEMPLATES = "templates"
    HEALTH_HISTORY = "health_check_history"
    SKILL_USAGE = "skill_usage_events"


SPECIAL: Final = frozenset(SpecialTable)
CATEGORIES: Final = (
    DIRECT_TENANT,
    PLATFORM_ONLY,
    GLOBAL_IDENTITY,
    GLOBAL_SELF,
    RUNTIME_EXEMPT,
    SPECIAL,
    frozenset(CHILDREN),
)
CLASSIFIED: Final = frozenset().union(*CATEGORIES)


class RlsInventoryError(RuntimeError):
    def __init__(self, tables: tuple[str, ...]) -> None:
        self.tables = tables
        super().__init__("Unclassified or invalid RLS tables: " + ", ".join(tables))


def inventory_problems(names: Iterable[str], columns: Mapping[str, Iterable[str]]) -> list[str]:
    """Unclassified tables and broken shapes; empty means m92 may install policies."""
    present = set(names)
    problems = sorted(present - CLASSIFIED)
    if sum(map(len, CATEGORIES)) != len(CLASSIFIED):
        problems.append("duplicate classification")
    for name in sorted(present & DIRECT_TENANT):
        if "tenant_id" not in set(columns.get(name, ())):
            problems.append(name + ".tenant_id")
    for name in sorted(present & CHILDREN.keys()):
        local = set(columns.get(name, ()))
        for parent in CHILDREN[name].parents:
            if parent.local_column not in local or parent.table not in present:
                problems.append(name + "." + parent.local_column)
    return problems


def protected_tables(names: Iterable[str]) -> tuple[str, ...]:
    """Physical tables that receive the ``tenant_boundary`` policy (all but exempt)."""
    return tuple(sorted(set(names) - RUNTIME_EXEMPT))


def classified_tables(connection: Connection) -> tuple[str, ...]:
    """Inspect the live schema and fail before any DDL when it is not fully reviewed."""
    inspector = sa.inspect(connection)
    names = inspector.get_table_names()
    columns = {name: [c["name"] for c in inspector.get_columns(name)] for name in names}
    problems = inventory_problems(names, columns)
    if problems:
        raise RlsInventoryError(tuple(problems))
    return protected_tables(names)
