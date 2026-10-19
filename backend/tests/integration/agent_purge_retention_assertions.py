"""Exact retained and deleted identity checks for native Agent purge."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.agent_lifecycle_fixture import LifecycleGraph, LifecycleGraphProof


async def _ids(db: AsyncSession, query: str, ids: Sequence[UUID]) -> tuple[str, ...]:
    values = await db.scalars(text(query), {"ids": list(ids)})
    return tuple(sorted(str(value) for value in values))


async def assert_purge_retention_inventory(
    db: AsyncSession,
    graph: LifecycleGraph,
    before: LifecycleGraphProof,
) -> None:
    """Compare every fixture-owned retained identity and removed operational identity."""
    expected = dict(before.row_ids)
    retained = (
        (
            "message_events",
            "SELECT id FROM message_events WHERE conversation_id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "message_event_chunks",
            "SELECT id FROM message_event_chunks WHERE conversation_id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "message_feedbacks",
            "SELECT id FROM message_feedbacks WHERE conversation_id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "conversation_pinned_summaries",
            "SELECT conversation_id FROM conversation_pinned_summaries "
            "WHERE conversation_id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "conversation_run_metrics",
            "SELECT run_id FROM conversation_run_metrics WHERE run_id = ANY(:ids)",
            graph.run_ids,
        ),
        (
            "token_usages",
            "SELECT id FROM token_usages WHERE run_id = ANY(:ids)",
            graph.run_ids,
        ),
        (
            "agent_api_threads",
            "SELECT id FROM agent_api_threads WHERE conversation_id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "mcp_servers",
            "SELECT mcp_server_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            (graph.mcp_binding_id,),
        ),
        (
            "mcp_tools",
            "SELECT mcp_tool_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            (graph.mcp_binding_id,),
        ),
    )
    for surface, query, owner_ids in retained:
        assert await _ids(db, query, owner_ids) == expected[surface]
    removed = (
        (
            "SELECT parent_agent_id FROM agent_subagents WHERE parent_agent_id = ANY(:ids)",
            (graph.root_agent_id,),
        ),
        (
            "SELECT id FROM agent_deployments WHERE agent_id = ANY(:ids)",
            (graph.root_agent_id,),
        ),
        (
            "SELECT id FROM agent_triggers WHERE agent_id = ANY(:ids)",
            (graph.root_agent_id,),
        ),
    )
    for query, owner_ids in removed:
        assert await _ids(db, query, owner_ids) == ()


__all__ = ["assert_purge_retention_inventory"]
