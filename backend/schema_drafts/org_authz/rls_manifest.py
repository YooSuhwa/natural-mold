"""Reviewed physical-table inventory: FK constraints never propagate RLS.

Global identity rows are deliberately separate from platform source/history.
New physical tables must be classified here before the deferred m92 can run.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

import sqlalchemy as sa
from sqlalchemy.engine import Connection

DIRECT_TENANT: Final = frozenset(
    {
        "access_requests",
        "agent_api_keys",
        "agent_blueprints",
        "agent_deployments",
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
        "tenant_members",
        "tenant_role_grants",
        "token_usages",
        "tools",
    }
)
PLATFORM_CATALOG: Final = frozenset(
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
PLATFORM_ONLY: Final = frozenset(
    {
        "alembic_version",
        "authz_outbox",
        "authz_model_versions",
        "authz_shadow_diffs",
        "_m11_dedup_connection_snapshot",
        "_m11_dedup_tool_remap",
        "_m11_tool_backfill_provenance",
        "_m9_migrated_connections",
        "agent_creation_sessions",
        # LangGraph creates these outside Alembic. Only the trusted checkpoint
        # adapter may access them after checking conversation tenant/ownership.
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)
GLOBAL_IDENTITY: Final = frozenset({"users", "refresh_tokens", "user_identities"})
GLOBAL_SELF: Final = frozenset({"user_memory_settings"})


class SpecialTable(StrEnum):
    TENANTS = "tenants"
    TEMPLATES = "templates"
    HEALTH_HISTORY = "health_check_history"
    SKILL_USAGE = "skill_usage_events"


SPECIAL: Final = frozenset(SpecialTable)


@dataclass(frozen=True, slots=True)
class Parent:
    table: str
    local_column: str
    optional: bool = False
    catalog_reference: bool = False


@dataclass(frozen=True, slots=True)
class ChildRule:
    parents: tuple[Parent, ...]
    catalog_read: bool = False


# Every parent is checked, including secondary links that could otherwise be
# reassigned to a foreign tenant while leaving the primary owner unchanged.
# Nullable links are checked when present; the non-null owner is never optional.
CHILDREN: Final = MappingProxyType(
    {
        "agent_mcp_tools": ChildRule(
            (
                Parent("agents", "agent_id"),
                Parent("mcp_tools", "mcp_tool_id", catalog_reference=True),
            )
        ),
        "agent_skills": ChildRule(
            (Parent("agents", "agent_id"), Parent("skills", "skill_id", catalog_reference=True))
        ),
        "agent_tools": ChildRule(
            (Parent("agents", "agent_id"), Parent("tools", "tool_id", catalog_reference=True))
        ),
        "agent_subagents": ChildRule(
            (Parent("agents", "parent_agent_id"), Parent("agents", "sub_agent_id"))
        ),
        "agent_memory_settings": ChildRule((Parent("agents", "agent_id"),)),
        "agent_user_preferences": ChildRule((Parent("agents", "agent_id"),)),
        "agent_api_key_deployments": ChildRule(
            (Parent("agent_api_keys", "api_key_id"), Parent("agent_deployments", "deployment_id"))
        ),
        "agent_api_threads": ChildRule(
            (
                Parent("conversations", "conversation_id"),
                Parent("agent_deployments", "deployment_id"),
            )
        ),
        "agent_api_runs": ChildRule(
            (
                Parent("agent_deployments", "deployment_id"),
                Parent("agent_api_threads", "thread_id", optional=True),
                Parent("conversations", "conversation_id", optional=True),
                Parent("agent_api_keys", "api_key_id", optional=True),
            )
        ),
        "agent_trigger_runs": ChildRule(
            (
                Parent("agent_triggers", "trigger_id"),
                Parent("agents", "agent_id"),
                Parent("conversations", "conversation_id", optional=True),
            )
        ),
        "artifact_versions": ChildRule((Parent("conversation_artifacts", "artifact_id"),)),
        "conversation_pinned_summaries": ChildRule((Parent("conversations", "conversation_id"),)),
        "conversation_run_inputs": ChildRule(
            (
                Parent("conversations", "conversation_id"),
                Parent("agents", "agent_id"),
                Parent("conversation_runs", "run_id", optional=True),
            )
        ),
        "conversation_run_metrics": ChildRule((Parent("conversation_runs", "run_id"),)),
        "credential_audit_logs": ChildRule((Parent("credentials", "credential_id"),)),
        "credential_oauth_states": ChildRule((Parent("credentials", "credential_id"),)),
        "marketplace_item_acl": ChildRule((Parent("marketplace_items", "item_id"),)),
        "marketplace_versions": ChildRule(
            (Parent("marketplace_items", "item_id"),), catalog_read=True
        ),
        "marketplace_publication_links": ChildRule(
            (
                Parent("marketplace_items", "item_id"),
                Parent("agents", "source_agent_id", optional=True),
                Parent("mcp_servers", "source_mcp_server_id", optional=True),
                Parent("skills", "source_skill_id", optional=True),
            )
        ),
        "mcp_tools": ChildRule((Parent("mcp_servers", "server_id"),), catalog_read=True),
        "mcp_app_invocation_bindings": ChildRule(
            (
                Parent("conversations", "conversation_id"),
                Parent("conversation_runs", "run_id"),
                Parent("agents", "agent_id"),
                Parent("mcp_servers", "mcp_server_id", catalog_reference=True),
                Parent("mcp_tools", "mcp_tool_id", catalog_reference=True),
            )
        ),
        "message_events": ChildRule((Parent("conversations", "conversation_id"),)),
        "message_event_chunks": ChildRule(
            (
                Parent("conversations", "conversation_id"),
                Parent("message_events", "message_event_id"),
            )
        ),
        "message_feedbacks": ChildRule((Parent("conversations", "conversation_id"),)),
        "skill_credential_bindings": ChildRule(
            (Parent("skills", "skill_id"), Parent("credentials", "credential_id"))
        ),
        "skill_evaluation_sets": ChildRule((Parent("skills", "skill_id"),)),
        "skill_evaluation_runs": ChildRule(
            (
                Parent("skills", "skill_id"),
                Parent("skill_evaluation_sets", "evaluation_set_id", optional=True),
            )
        ),
        "skill_evaluation_case_feedbacks": ChildRule((Parent("skill_evaluation_runs", "run_id"),)),
        "skill_feedbacks": ChildRule((Parent("skills", "skill_id"),)),
        "skill_revisions": ChildRule(
            (
                Parent("skills", "skill_id"),
                Parent("skill_builder_sessions", "source_session_id", optional=True),
            )
        ),
    }
)


class RlsInventoryError(RuntimeError):
    """Exceptions remain mutable so context managers can attach tracebacks."""

    def __init__(self, tables: tuple[str, ...]) -> None:
        self.tables = tables
        super().__init__("Unclassified or invalid RLS tables: " + ", ".join(tables))

    def __str__(self) -> str:
        return "Unclassified or invalid RLS tables: " + ", ".join(self.tables)


def classified_tables(connection: Connection) -> tuple[str, ...]:
    """Fail before policy DDL when the physical inventory or scope shape changed."""
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names())
    categories = (
        DIRECT_TENANT,
        PLATFORM_ONLY,
        GLOBAL_IDENTITY,
        GLOBAL_SELF,
        SPECIAL,
        frozenset(CHILDREN),
    )
    classified = frozenset().union(*categories)
    invalid = names - classified
    if sum(map(len, categories)) != len(classified):
        raise RlsInventoryError(("duplicate classification",))
    for name in names & DIRECT_TENANT:
        columns = {column["name"] for column in inspector.get_columns(name)}
        if "tenant_id" not in columns:
            invalid.add(name + ".tenant_id")
    for name in names & CHILDREN.keys():
        columns = {column["name"] for column in inspector.get_columns(name)}
        for parent in CHILDREN[name].parents:
            if parent.local_column not in columns or parent.table not in names:
                invalid.add(name + "." + parent.local_column)
    if invalid:
        raise RlsInventoryError(tuple(sorted(invalid)))
    return tuple(sorted(names))
