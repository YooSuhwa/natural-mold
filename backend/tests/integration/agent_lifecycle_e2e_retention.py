"""Pre-purge retained identity proof for the Task 15 workerless flow."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_event import MessageEvent, MessageEventChunk
from tests.authz.agent_lifecycle_fixture import (
    LifecycleGraph,
    LifecycleGraphProof,
    RegisteredLifecycleFixture,
)


async def _ids(
    db: AsyncSession,
    query: str,
    owner_ids: tuple[UUID, ...],
) -> tuple[str, ...]:
    values = await db.scalars(text(query), {"ids": list(owner_ids)})
    return tuple(sorted(str(value) for value in values))


async def capture_pre_purge_retention(
    fixture: RegisteredLifecycleFixture,
    seeded: LifecycleGraphProof,
) -> LifecycleGraphProof:
    """Prove the workerless delta, then return its separate immutable inventory."""
    graph = fixture.graph
    seeded_ids = dict(seeded.row_ids)
    async with fixture.sessions() as db:
        actual_ids = await _retained_ids(db, graph)
        events = tuple(
            (
                await db.execute(
                    select(
                        MessageEvent.id,
                        MessageEvent.assistant_msg_id,
                        MessageEvent.last_event_id,
                        MessageEvent.status,
                    ).where(MessageEvent.assistant_msg_id.in_(map(str, graph.run_ids)))
                )
            ).all()
        )
        chunks = tuple(
            (
                await db.execute(
                    select(
                        MessageEventChunk.id,
                        MessageEventChunk.message_event_id,
                        MessageEventChunk.assistant_msg_id,
                        MessageEventChunk.first_event_id,
                        MessageEventChunk.last_event_id,
                        MessageEventChunk.event_ids,
                        MessageEventChunk.events,
                    ).where(MessageEventChunk.assistant_msg_id.in_(map(str, graph.run_ids)))
                )
            ).all()
        )

    expected_assistants = {str(run_id) for run_id in graph.run_ids}
    assert {row.assistant_msg_id for row in events} == expected_assistants
    assert {row.assistant_msg_id for row in chunks} == expected_assistants
    event_ids_by_assistant = {row.assistant_msg_id: row.id for row in events}
    for row in events:
        terminal_id = f"{row.assistant_msg_id}-canceled"
        assert row.last_event_id == terminal_id and row.status == "completed"
    for row in chunks:
        terminal_id = f"{row.assistant_msg_id}-canceled"
        assert row.message_event_id == event_ids_by_assistant[row.assistant_msg_id]
        assert row.first_event_id == row.last_event_id == terminal_id
        assert row.event_ids == [terminal_id]
        assert row.events == [
            {
                "id": terminal_id,
                "event": "message_end",
                "data": {"usage": {}, "content": "", "status": "canceled"},
            }
        ]

    actual = dict(actual_ids)
    terminal_event_ids = {str(row.id) for row in events}
    terminal_chunk_ids = {str(row.id) for row in chunks}
    seeded_events = set(seeded_ids["message_events"])
    seeded_chunks = set(seeded_ids["message_event_chunks"])
    assert terminal_event_ids.isdisjoint(seeded_events)
    assert terminal_chunk_ids.isdisjoint(seeded_chunks)
    assert set(actual["message_events"]) - seeded_events == terminal_event_ids
    assert set(actual["message_event_chunks"]) - seeded_chunks == terminal_chunk_ids
    assert set(actual["message_events"]) == seeded_events | terminal_event_ids
    assert set(actual["message_event_chunks"]) == seeded_chunks | terminal_chunk_ids
    assert len(terminal_event_ids) == len(terminal_chunk_ids) == len(graph.run_ids) == 3
    for surface, identities in actual_ids:
        if surface not in {"message_events", "message_event_chunks"}:
            assert identities == seeded_ids[surface]
    return replace(seeded, row_ids=actual_ids)


async def _retained_ids(
    db: AsyncSession,
    graph: LifecycleGraph,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    conversations = graph.conversation_ids
    runs = graph.run_ids
    bindings = (graph.mcp_binding_id,)
    specifications = (
        (
            "message_events",
            "SELECT id FROM message_events WHERE conversation_id = ANY(:ids)",
            conversations,
        ),
        (
            "message_event_chunks",
            "SELECT id FROM message_event_chunks WHERE conversation_id = ANY(:ids)",
            conversations,
        ),
        (
            "message_feedbacks",
            "SELECT id FROM message_feedbacks WHERE conversation_id = ANY(:ids)",
            conversations,
        ),
        (
            "conversation_pinned_summaries",
            "SELECT conversation_id FROM conversation_pinned_summaries "
            "WHERE conversation_id = ANY(:ids)",
            conversations,
        ),
        (
            "conversation_run_metrics",
            "SELECT run_id FROM conversation_run_metrics WHERE run_id = ANY(:ids)",
            runs,
        ),
        ("token_usages", "SELECT id FROM token_usages WHERE run_id = ANY(:ids)", runs),
        (
            "agent_api_threads",
            "SELECT id FROM agent_api_threads WHERE conversation_id = ANY(:ids)",
            conversations,
        ),
        (
            "mcp_servers",
            "SELECT mcp_server_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            bindings,
        ),
        (
            "mcp_tools",
            "SELECT mcp_tool_id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            bindings,
        ),
    )
    inventory: list[tuple[str, tuple[str, ...]]] = []
    for surface, query, owner_ids in specifications:
        inventory.append((surface, await _ids(db, query, owner_ids)))
    return tuple(inventory)


__all__ = ["capture_pre_purge_retention"]
