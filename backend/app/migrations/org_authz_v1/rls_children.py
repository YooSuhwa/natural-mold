"""m92 (I-06): child tables whose company comes from RLS-protected parents.

Every parent is checked, including secondary links that could otherwise be
reassigned to a foreign company while the primary owner stays unchanged.
Nullable links are checked when present; the non-null owner is never optional.
Foreign keys never propagate RLS, so each parent is re-read under its own policy.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Parent:
    table: str
    local_column: str
    optional: bool = False
    # The parent may be a platform catalog row (tenant_id NULL), e.g. a system tool.
    catalog_reference: bool = False


@dataclass(frozen=True, slots=True)
class ChildRule:
    parents: tuple[Parent, ...]
    # Readers also see children of platform catalog parents (mcp tools of system servers).
    catalog_read: bool = False


def _rule(*parents: Parent, catalog_read: bool = False) -> ChildRule:
    return ChildRule(parents, catalog_read)


_CONVERSATION = Parent("conversations", "conversation_id")
_MANIFEST = Parent("conversation_history_manifests", "manifest_id")

CHILDREN: Final = MappingProxyType(
    {
        "agent_mcp_tools": _rule(
            Parent("agents", "agent_id"),
            Parent("mcp_tools", "mcp_tool_id", catalog_reference=True),
        ),
        "agent_skills": _rule(
            Parent("agents", "agent_id"), Parent("skills", "skill_id", catalog_reference=True)
        ),
        "agent_tools": _rule(
            Parent("agents", "agent_id"), Parent("tools", "tool_id", catalog_reference=True)
        ),
        "agent_subagents": _rule(
            Parent("agents", "parent_agent_id"), Parent("agents", "sub_agent_id")
        ),
        "agent_memory_settings": _rule(Parent("agents", "agent_id")),
        "agent_user_preferences": _rule(Parent("agents", "agent_id")),
        "agent_api_key_deployments": _rule(
            Parent("agent_api_keys", "api_key_id"), Parent("agent_deployments", "deployment_id")
        ),
        "agent_api_threads": _rule(_CONVERSATION, Parent("agent_deployments", "deployment_id")),
        "agent_purge_cleanup_items": _rule(Parent("agent_purge_receipts", "receipt_id")),
        "artifact_versions": _rule(Parent("conversation_artifacts", "artifact_id")),
        "conversation_history_manifests": _rule(_CONVERSATION),
        "conversation_history_checkpoints": _rule(_MANIFEST, _CONVERSATION),
        "conversation_history_events": _rule(_MANIFEST, _CONVERSATION),
        "conversation_pinned_summaries": _rule(_CONVERSATION),
        "conversation_run_agents": _rule(
            Parent("conversation_runs", "run_id"), Parent("agents", "agent_id", optional=True)
        ),
        "conversation_run_inputs": _rule(
            _CONVERSATION,
            Parent("agents", "agent_id"),
            Parent("conversation_runs", "run_id", optional=True),
        ),
        "conversation_run_metrics": _rule(Parent("conversation_runs", "run_id")),
        "credential_audit_logs": _rule(Parent("credentials", "credential_id")),
        "credential_oauth_states": _rule(Parent("credentials", "credential_id")),
        "marketplace_item_acl": _rule(Parent("marketplace_items", "item_id")),
        "marketplace_versions": _rule(Parent("marketplace_items", "item_id"), catalog_read=True),
        "marketplace_publication_links": _rule(
            Parent("marketplace_items", "item_id"),
            Parent("agents", "source_agent_id", optional=True),
            Parent("mcp_servers", "source_mcp_server_id", optional=True),
            Parent("skills", "source_skill_id", optional=True),
        ),
        "mcp_tools": _rule(Parent("mcp_servers", "server_id"), catalog_read=True),
        "mcp_app_invocation_bindings": _rule(
            _CONVERSATION,
            Parent("conversation_runs", "run_id"),
            Parent("agents", "agent_id"),
            Parent("mcp_servers", "mcp_server_id", catalog_reference=True),
            Parent("mcp_tools", "mcp_tool_id", catalog_reference=True),
        ),
        "message_events": _rule(_CONVERSATION),
        "message_event_chunks": _rule(_CONVERSATION, Parent("message_events", "message_event_id")),
        "message_feedbacks": _rule(_CONVERSATION),
        "skill_credential_bindings": _rule(
            Parent("skills", "skill_id"), Parent("credentials", "credential_id")
        ),
        "skill_evaluation_sets": _rule(Parent("skills", "skill_id")),
        "skill_evaluation_runs": _rule(
            Parent("skills", "skill_id"),
            Parent("skill_evaluation_sets", "evaluation_set_id", optional=True),
        ),
        "skill_evaluation_case_feedbacks": _rule(Parent("skill_evaluation_runs", "run_id")),
        "skill_feedbacks": _rule(Parent("skills", "skill_id")),
        "skill_revisions": _rule(
            Parent("skills", "skill_id"),
            Parent("skill_builder_sessions", "source_session_id", optional=True),
        ),
    }
)
