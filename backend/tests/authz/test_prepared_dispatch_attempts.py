"""I20 dispatch attempts: exact graph only, no rebuild, no post-marker preparation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner, runtime_component_builder, subagents
from app.agent_runtime.prepared_run_dispatch import (
    PreparedRunHandle,
    handle_for,
    prepared_dispatch,
)
from app.agent_runtime.prepared_run_registry import (
    LEASE_DUPLICATE,
    LEASE_MISMATCH,
    LEASE_MISSING,
    LeaseState,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.agent_runtime.runtime_hydration_guard import HydrationDeniedError, active_lease_run
from app.services import chat_service
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import PostMarkerSpies


def _delegating(seeded: akit.Seeded) -> dict[str, list[AIMessage]]:
    return {
        seeded.root_model: [kit.task_call(seeded.child_name, "call-t"), AIMessage("done")],
        seeded.child_model: [AIMessage("helped")],
    }


@pytest.mark.asyncio
async def test_dispatch_cannot_replace_or_recompile_prepared_task_handler(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _delegating)
    lease = active.lease
    graph, children = lease.runnable, dict(lease.child_runnables)
    tools_before = dict(graph.nodes["tools"].bound.tools_by_name)
    task_tool = tools_before["task"]
    streamed: list[Any] = []
    stream = agent_stream_runner.stream_agent_response

    def recording_stream(agent: Any, *args: Any, **kwargs: Any) -> Any:
        streamed.append((agent, active_lease_run()))
        return stream(agent, *args, **kwargs)

    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("post-marker preparation or child compilation")

    monkeypatch.setattr(agent_stream_runner, "stream_agent_response", recording_stream)
    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", forbidden)
    monkeypatch.setattr(subagents, "_prepare_runtime_components", forbidden)
    spies = PostMarkerSpies(monkeypatch)
    cfg = active.prepare.execution_cfg
    assert cfg is not None

    chunks = [
        c async for c in agent_stream_runner._run_prepared_agent_stream(cfg, handle_for(lease))
    ]

    assert any("done" in chunk for chunk in chunks)
    assert streamed == [(graph, str(active.seeded.run_id))]
    assert lease.runnable is graph and dict(lease.child_runnables) == children
    tools_after = graph.nodes["tools"].bound.tools_by_name
    assert tools_after == tools_before and tools_after["task"] is task_tool
    assert spies.compiled == [] and "task" not in spies.executed
    assert [
        active.seeded.root_model,
        active.seeded.child_model,
        active.seeded.root_model,
    ] == kit.SENDS


@pytest.mark.asyncio
async def test_preparation_seams_fail_closed_inside_dispatch(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _delegating)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    conversation = await chat_service.get_owned_conversation_with_agent(
        db, active.seeded.graph.conversation_ids[0], active.seeded.graph.context
    )
    assert conversation is not None and conversation.agent is not None
    async with prepared_dispatch(handle_for(active.lease)):
        with pytest.raises(HydrationDeniedError) as prepare:
            await runtime_component_builder._prepare_agent(cfg, messages_history=[])
        with pytest.raises(HydrationDeniedError) as children:
            await subagents.build_subagents_config(
                conversation.agent, db=db, parent_cfg=cfg, is_trigger_mode=False
            )
    assert prepare.value.seam == "_prepare_agent"
    assert children.value.seam == "build_subagents_config"
    assert kit.SENDS == [] and get_prepared_run_registry().state(active.seeded.run_id) is None


@pytest.mark.asyncio
async def test_mismatched_or_missing_handle_never_rebuilds(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = await akit.activate(db, tmp_path, monkeypatch, _delegating)
    lease, registry = active.lease, get_prepared_run_registry()
    resolved: list[Any] = []

    def no_rebuild(*args: Any, **kwargs: Any) -> Any:
        resolved.append(args)
        raise AssertionError("rebuild")

    monkeypatch.setattr(akit.adapter, "resolve_agent_context", no_rebuild)
    cfg = active.prepare.execution_cfg
    assert cfg is not None
    for forged in (
        PreparedRunHandle(lease.binding, "forged", lease.digest),
        PreparedRunHandle(lease.binding, lease.token, "0" * 64),
    ):
        with pytest.raises(PreparedRunLeaseError) as mismatch:
            await agent_stream_runner.execute_prepared_agent_invoke(cfg, forged)
        assert mismatch.value.code == LEASE_MISMATCH
    assert registry.state(lease.binding.run_id) is LeaseState.ACTIVE and kit.SENDS == []

    assert await agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(lease)) == "done"
    with pytest.raises(PreparedRunLeaseError) as missing:
        await agent_stream_runner.execute_prepared_agent_invoke(cfg, handle_for(lease))
    assert missing.value.code == LEASE_MISSING
    with pytest.raises(PreparedRunLeaseError) as duplicate:
        registry.reserve(lease.binding)
    assert duplicate.value.code == LEASE_DUPLICATE and resolved == []
