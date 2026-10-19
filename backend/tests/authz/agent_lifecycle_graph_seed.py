"""Compose and verify the complete B08 native lifecycle graph."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from tests.authz.agent_lifecycle_fixture import (
    LifecycleActors,
    LifecycleGraph,
    LifecycleGraphProof,
    LifecycleSentinel,
)
from tests.authz.agent_lifecycle_graph_history import (
    ARTIFACT_VERSION_BYTES,
    ATTACHMENT_BYTES,
    seed_history_rows,
)
from tests.authz.agent_lifecycle_graph_proof import collect_retained_surface_ids
from tests.authz.agent_lifecycle_graph_rows import seed_core_rows


def _graph(actors: LifecycleActors) -> LifecycleGraph:
    run_root = Path(os.environ["MOLDY_TEST_RUN_ROOT"])
    content = run_root / "b08-full-graph"
    return LifecycleGraph(
        tenant_id=actors.tenant_id,
        org_id=actors.org_id,
        root_owner_id=actors.owner_one_id,
        child_owner_id=actors.owner_two_id,
        root_agent_id=uuid4(),
        child_agent_ids=(uuid4(), uuid4()),
        conversation_ids=(uuid4(), uuid4(), uuid4()),
        run_ids=(uuid4(), uuid4(), uuid4()),
        input_ids=(uuid4(), uuid4(), uuid4()),
        attachment_id=uuid4(),
        artifact_id=uuid4(),
        artifact_version_ids=(uuid4(), uuid4()),
        api_run_id=uuid4(),
        trigger_run_id=uuid4(),
        share_link_id=uuid4(),
        mcp_binding_id=uuid4(),
        checkpoint_ids=(f"b08-root-{uuid4().hex}", f"b08-branch-{uuid4().hex}"),
        attachment_path=content / "attachment.bin",
        artifact_version_paths=(content / "artifact-v1.bin", content / "artifact-v2.bin"),
    )


def _seed_checkpoints(thread_id: UUID, checkpoint_ids: tuple[str, ...]) -> None:
    url = settings.database_url_sync
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        root = empty_checkpoint()
        root["id"] = checkpoint_ids[0]
        root["channel_values"] = {
            "messages": [HumanMessage(id="b08-user-message", content="fixture question")],
            "branch_header": {"name": "root", "fixture": True},
        }
        root["channel_versions"] = {"messages": "1", "branch_header": "1"}
        saved = saver.put(
            {"configurable": {"thread_id": str(thread_id), "checkpoint_ns": ""}},
            root,
            {"source": "input", "step": 0},
            {"messages": "1", "branch_header": "1"},
        )
        branch = empty_checkpoint()
        branch["id"] = checkpoint_ids[1]
        branch["channel_values"] = {
            "messages": [
                HumanMessage(id="b08-user-message", content="fixture question"),
                AIMessage(id="b08-assistant-message", content="fixture answer"),
            ],
            "branch_header": {"name": "branch-a", "parent": checkpoint_ids[0]},
        }
        branch["channel_versions"] = {"messages": "2", "branch_header": "2"}
        saver.put(
            saved,
            branch,
            {"source": "loop", "step": 1},
            {"messages": "2", "branch_header": "2"},
        )


async def seed_lifecycle_graph(
    sessions: async_sessionmaker[AsyncSession],
    actors: LifecycleActors,
    sentinel: LifecycleSentinel,
) -> LifecycleGraph:
    """Seed every lifecycle-owned execution and retained-history surface."""
    graph = _graph(actors)
    sentinel.observe("prepared")
    async with sessions() as db:
        await seed_core_rows(db, actors, graph)
        await seed_history_rows(db, actors, graph)
    await asyncio.to_thread(
        _seed_checkpoints,
        graph.conversation_ids[0],
        graph.checkpoint_ids,
    )
    sentinel.observe("started")
    return graph


async def assert_lifecycle_graph(
    sessions: async_sessionmaker[AsyncSession],
    graph: LifecycleGraph,
) -> LifecycleGraphProof:
    """Prove exact row identities, file bytes, checksums, and checkpoint branches."""
    identity_sets = (
        (
            "agents",
            "SELECT id FROM agents WHERE id = ANY(:ids)",
            (graph.root_agent_id, *graph.child_agent_ids),
        ),
        (
            "conversations",
            "SELECT id FROM conversations WHERE id = ANY(:ids)",
            graph.conversation_ids,
        ),
        (
            "conversation_runs",
            "SELECT id FROM conversation_runs WHERE id = ANY(:ids)",
            graph.run_ids,
        ),
        (
            "conversation_run_inputs",
            "SELECT id FROM conversation_run_inputs WHERE id = ANY(:ids)",
            graph.input_ids,
        ),
        (
            "message_attachments",
            "SELECT id FROM message_attachments WHERE id = ANY(:ids)",
            (graph.attachment_id,),
        ),
        (
            "conversation_artifacts",
            "SELECT id FROM conversation_artifacts WHERE id = ANY(:ids)",
            (graph.artifact_id,),
        ),
        (
            "artifact_versions",
            "SELECT id FROM artifact_versions WHERE id = ANY(:ids)",
            graph.artifact_version_ids,
        ),
        (
            "agent_api_runs",
            "SELECT id FROM agent_api_runs WHERE id = ANY(:ids)",
            (graph.api_run_id,),
        ),
        (
            "agent_trigger_runs",
            "SELECT id FROM agent_trigger_runs WHERE id = ANY(:ids)",
            (graph.trigger_run_id,),
        ),
        (
            "share_links",
            "SELECT id FROM share_links WHERE id = ANY(:ids)",
            (graph.share_link_id,),
        ),
        (
            "mcp_app_invocation_bindings",
            "SELECT id FROM mcp_app_invocation_bindings WHERE id = ANY(:ids)",
            (graph.mcp_binding_id,),
        ),
    )
    counts: list[tuple[str, int]] = []
    async with sessions() as db:
        for table, query, expected in identity_sets:
            rows = await db.scalars(
                text(query),
                {"ids": list(expected)},
            )
            actual = {UUID(str(value)) for value in rows}
            assert actual == set(expected)
            counts.append((table, len(actual)))
        participant_count = await db.scalar(
            text("SELECT count(*) FROM conversation_run_agents WHERE run_id = ANY(:ids)"),
            {"ids": list(graph.run_ids)},
        )
        assert participant_count == 9
        counts.append(("conversation_run_agents", 9))
        checkpoint_rows = await db.execute(
            text(
                "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints "
                "WHERE thread_id = :thread ORDER BY checkpoint_id"
            ),
            {"thread": str(graph.conversation_ids[0])},
        )
        checkpoint_pairs = set(checkpoint_rows.tuples())
        assert {pair[0] for pair in checkpoint_pairs} == set(graph.checkpoint_ids)
        assert (graph.checkpoint_ids[1], graph.checkpoint_ids[0]) in checkpoint_pairs
        counts.append(("checkpoints", len(checkpoint_pairs)))
        retained_ids = await collect_retained_surface_ids(db, graph)
        counts.extend((surface, len(identities)) for surface, identities in retained_ids)
    attachment = await anyio.Path(graph.attachment_path).read_bytes()
    versions = tuple([await anyio.Path(path).read_bytes() for path in graph.artifact_version_paths])
    assert attachment == ATTACHMENT_BYTES
    assert versions == ARTIFACT_VERSION_BYTES
    return LifecycleGraphProof(
        row_counts=tuple(counts),
        row_ids=retained_ids,
        attachment_bytes=len(attachment),
        artifact_version_bytes=(len(versions[0]), len(versions[1])),
        attachment_sha256=hashlib.sha256(attachment).hexdigest(),
        artifact_version_sha256=(
            hashlib.sha256(versions[0]).hexdigest(),
            hashlib.sha256(versions[1]).hexdigest(),
        ),
    )


__all__ = ["assert_lifecycle_graph", "seed_lifecycle_graph"]
