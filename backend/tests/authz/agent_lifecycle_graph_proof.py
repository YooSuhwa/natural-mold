"""Exact retained-surface identities for the B08 lifecycle graph."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.agent_lifecycle_fixture import LifecycleGraph


async def _identities(
    db: AsyncSession,
    query: str,
    ids: Sequence[UUID],
) -> tuple[str, ...]:
    rows = await db.scalars(text(query), {"ids": list(ids)})
    return tuple(sorted(str(value) for value in rows))


async def collect_retained_surface_ids(
    db: AsyncSession,
    graph: LifecycleGraph,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Read every seeded execution/history surface by its owning graph IDs."""
    root = (graph.root_agent_id,)
    conversations = graph.conversation_ids
    runs = graph.run_ids
    bindings = (graph.mcp_binding_id,)
    specifications = (
        (
            "agent_subagents",
            "SELECT parent_agent_id::text || '>' || sub_agent_id::text "
            "FROM agent_subagents WHERE parent_agent_id = ANY(:ids)",
            root,
            2,
        ),
        (
            "message_events",
            "SELECT id FROM message_events WHERE conversation_id = ANY(:ids)",
            conversations,
            1,
        ),
        (
            "message_event_chunks",
            "SELECT id FROM message_event_chunks WHERE conversation_id = ANY(:ids)",
            conversations,
            1,
        ),
        (
            "message_feedbacks",
            "SELECT id FROM message_feedbacks WHERE conversation_id = ANY(:ids)",
            conversations,
            1,
        ),
        (
            "conversation_pinned_summaries",
            "SELECT conversation_id FROM conversation_pinned_summaries "
            "WHERE conversation_id = ANY(:ids)",
            conversations,
            1,
        ),
        (
            "conversation_run_metrics",
            "SELECT run_id FROM conversation_run_metrics WHERE run_id = ANY(:ids)",
            runs,
            3,
        ),
        (
            "token_usages",
            "SELECT id FROM token_usages WHERE run_id = ANY(:ids)",
            runs,
            1,
        ),
        (
            "agent_deployments",
            "SELECT id FROM agent_deployments WHERE agent_id = ANY(:ids)",
            root,
            1,
        ),
        (
            "agent_api_threads",
            "SELECT id FROM agent_api_threads WHERE conversation_id = ANY(:ids)",
            conversations,
            1,
        ),
        (
            "agent_triggers",
            "SELECT id FROM agent_triggers WHERE agent_id = ANY(:ids)",
            root,
            1,
        ),
        (
            "mcp_servers",
            "SELECT mcp_server_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            bindings,
            1,
        ),
        (
            "mcp_tools",
            "SELECT mcp_tool_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            bindings,
            1,
        ),
    )
    proof: list[tuple[str, tuple[str, ...]]] = []
    for surface, query, owner_ids, expected_count in specifications:
        identities = await _identities(db, query, owner_ids)
        assert len(identities) == expected_count
        proof.append((surface, identities))
    return tuple(proof)


__all__ = ["collect_retained_surface_ids"]
