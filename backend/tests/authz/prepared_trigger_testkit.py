"""Seed a scheduled trigger and run it through the prepared-lease trigger entry (I18).

The root agent (stored runtime policy, lease-supported provider) has one
trigger-safe MCP tool (trusted local URL, read-only name) and optionally one
stored linked child. Models are the I15 scripted, permit-guarded fakes and MCP
discovery is the I15 fake server, injected through ``prepared_run_inputs.materializer_seams``. LLM
keys are patched (no credential rows). The activation hook records the
provider-send and discovery counts at the moment the lease becomes active.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import (
    agent_stream_runner,
    credential_resolution,
    prepared_run_inputs,
    trigger_executor,
)
from app.config import settings
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.services import prepared_run_children, prepared_trigger_entry, prepared_trigger_resolver
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.conftest import TestSession

MCP_TOOL = "search_docs"
INPUT = "오늘 뉴스 요약"


@dataclass
class SeededTrigger:
    graph: GatewayGraph
    trigger_id: uuid.UUID
    root_model: str
    child_model: str | None


@dataclass
class Harness:
    seeded: SeededTrigger
    server: kit.FakeMcpServer
    factory: kit.ScriptedFactory
    at_activation: list[tuple[int, int]] = field(default_factory=list)
    legacy_invokes: list[str] = field(default_factory=list)
    hydrations: list[str] = field(default_factory=list)


async def seed_trigger(
    db: AsyncSession,
    *,
    stored_policy: bool = True,
    with_child: bool = False,
    child_identity: str = "fixed",
) -> SeededTrigger:
    graph = await seed_graph(db, conversations=0)
    ctx, key = graph.context, uuid.uuid4().hex[:8]
    agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    agent.runtime_policy = {"version": 1} if stored_policy else None
    root_model = await db.get(Model, agent.model_id)
    assert root_model is not None
    root_model.provider, root_model.model_name = "anthropic", f"root-{key}"
    server = McpServer(
        tenant_id=ctx.tenant_id,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        name="Docs server",
        transport="streamable_http",
        url="http://localhost:18001/mcp",
        is_system=False,
    )
    db.add(server)
    await db.flush()
    tool = McpTool(server_id=server.id, name=MCP_TOOL, input_schema={"type": "object"})
    db.add(tool)
    await db.flush()
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id))
    child_name = None
    if with_child:
        child_name = f"child-{key}"
        child_model = Model(provider="anthropic", model_name=child_name, display_name="C")
        db.add(child_model)
        await db.flush()
        child = Agent(
            user_id=ctx.user_id,
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            name="Researcher",
            description="Looks things up",
            model_id=child_model.id,
            system_prompt="child prompt",
            identity_mode=child_identity,
        )
        db.add(child)
        await db.flush()
        db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, position=0))
    trigger = AgentTrigger(
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        agent_id=agent.id,
        user_id=ctx.user_id,
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message=INPUT,
        status="active",
        run_count=0,
    )
    db.add(trigger)
    await db.commit()
    return SeededTrigger(graph, trigger.id, root_model.model_name, child_name)


def mcp_call(call_id: str) -> AIMessage:
    call = {"name": MCP_TOOL, "args": {}, "id": call_id}
    return AIMessage(content="", tool_calls=[call])


def install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    seeded: SeededTrigger,
    scripts: dict[str, list[AIMessage]],
    server: kit.FakeMcpServer | None = None,
) -> Harness:
    """Flag on + every fake seam; returns the counters the tests assert on."""
    kit.fresh_registry(monkeypatch)
    harness = Harness(
        seeded, server or kit.FakeMcpServer(names=(MCP_TOOL,)), kit.ScriptedFactory(scripts)
    )
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", True)
    monkeypatch.setattr(trigger_executor, "async_session", TestSession)

    async def root_key(*_a: Any, **_k: Any) -> str:
        return "sk-root"

    async def child_key(*_a: Any, **_k: Any) -> str:
        return "sk-child"

    monkeypatch.setattr(credential_resolution, "resolve_llm_api_key_for_agent", root_key)
    monkeypatch.setattr(prepared_run_children, "resolve_llm_api_key_for_agent", child_key)
    monkeypatch.setattr(
        prepared_run_inputs,
        "materializer_seams",
        lambda: kit.seams(harness.factory, harness.server),
    )
    monkeypatch.setattr(prepared_trigger_entry, "prepared_run_root_base", lambda: tmp_path / "runs")

    async def activated(_lease: Any) -> None:
        harness.at_activation.append((len(kit.SENDS), harness.server.discoveries))

    monkeypatch.setattr(prepared_trigger_entry, "_no_schedule", activated)

    async def legacy(request: Any) -> str:
        harness.legacy_invokes.append(request.input_message)
        return "legacy output"

    monkeypatch.setattr(trigger_executor, "_invoke_trigger", legacy)

    def hydrate(name: str) -> Callable[..., Any]:
        async def forbidden(*_a: Any, **_k: Any) -> Any:
            harness.hydrations.append(name)
            raise AssertionError(f"post-activation hydration: {name}")

        return forbidden

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", hydrate("_prepare_agent"))
    return harness


async def execute(seeded: SeededTrigger, *, force: bool = False) -> AgentTriggerRun | None:
    return await trigger_executor.execute_trigger(str(seeded.trigger_id), force=force)


async def trigger_runs(seeded: SeededTrigger) -> list[AgentTriggerRun]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == seeded.trigger_id)
        )
        return list(rows)


async def conversation_run(run_id: uuid.UUID | None) -> ConversationRun:
    assert run_id is not None
    async with TestSession() as db:
        row = await db.get(ConversationRun, run_id)
    assert row is not None
    return row


def before_locks(monkeypatch: pytest.MonkeyPatch, mutate: Any) -> Any:
    """Run ``mutate`` in another committed session just before the gateway locks."""
    real = prepared_trigger_resolver.acquire_execution_proof

    async def racing(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        async with TestSession() as other:
            await mutate(other)
            await other.commit()
        return await real(db, *args, **kwargs)

    monkeypatch.setattr(prepared_trigger_resolver, "acquire_execution_proof", racing)
    return real
