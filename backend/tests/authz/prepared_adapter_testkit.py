"""Seed real agent rows and materialize them through the prepared-run adapter.

Root agent + one stored linked child + one MCP tool link, a conversation with a
runtime-policy snapshot and a queued run. LLM keys are patched (no credential
rows); memory policy/context are injected through the bindings factory so the
resolver records that it ran under the gateway locks. Models are the I15
scripted, permit-guarded fakes; MCP discovery is the I15 fake server.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name
from app.agent_runtime.runtime_config import PreparedRunLease
from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy, resolve_runtime_policy
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.services import agent_execution_runtime, conversation_stream_service, prepared_run_children
from app.services import prepared_run_adapter as adapter
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from app.services.conversation_runtime_policy import _persist_snapshot
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph


@dataclass
class Seeded:
    graph: GatewayGraph
    run_id: uuid.UUID
    request: RuntimeOwnershipRequest
    user: CurrentUser
    root_model: str
    child_model: str
    child_name: str
    child_agent_id: uuid.UUID


STORED_POLICY = resolve_runtime_policy({"version": 1})


async def seed(
    db: AsyncSession,
    *,
    provider: str = "anthropic",
    with_child: bool = True,
    policy: ResolvedRuntimePolicy = STORED_POLICY,
    child_base_url: str | None = None,
) -> Seeded:
    graph = await seed_graph(db, conversations=1)
    agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    root_model = await db.get(Model, agent.model_id)
    assert root_model is not None
    key = uuid.uuid4().hex[:8]
    root_model.provider, root_model.model_name = provider, f"root-{key}"
    child_model = Model(
        provider="anthropic",
        model_name=f"child-{key}",
        display_name="Child",
        base_url=child_base_url,
    )
    ctx = graph.context
    server = McpServer(
        tenant_id=ctx.tenant_id,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        name="Lookup server",
        transport="streamable_http",
        url="https://mcp.test/a",
        headers={"Authorization": "Bearer mcp-secret"},
        is_system=False,
    )
    db.add_all((child_model, server))
    await db.flush()
    child = Agent(
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        name="Researcher",
        description="Looks things up",
        model_id=child_model.id,
        system_prompt="child prompt",
    )
    mcp_tool = McpTool(server_id=server.id, name="lookup", input_schema={"type": "object"})
    db.add_all((child, mcp_tool))
    await db.flush()
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    if with_child:
        db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, position=0))
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    assert conversation is not None
    _persist_snapshot(conversation, policy)
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=ctx.user_id,
        source="chat",
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.commit()
    request = RuntimeOwnershipRequest(run.id, conversation.id, agent.id, ctx)
    user = CurrentUser(id=ctx.user_id, email="u@example.com", name="U", is_super_user=False)
    return Seeded(
        graph,
        run.id,
        request,
        user,
        root_model.model_name,
        child_model.model_name,
        make_agent_runtime_name(child.id),
        child.id,
    )


@dataclass
class Memory:
    """Fake memory reads; records ``(kind, read_through_locked_session)``."""

    policy: str = "off"
    # Per-call write policies (root first, then each child); falls back to ``policy``.
    sequence: list[str] = field(default_factory=list)
    calls: list[tuple[str, bool]] = field(default_factory=list)

    def reads(self) -> adapter.MemoryReads:
        def locked(db: Any) -> bool:
            proof = None if db is None else live_proof(db)
            return proof is not None and proof.cursor.position is LockClass.RUN

        async def policy(_cfg: Any, db: Any, *, is_trigger_mode: bool) -> str:
            self.calls.append(("policy", locked(db)))
            return self.sequence.pop(0) if self.sequence else self.policy

        async def context(_cfg: Any, db: Any) -> tuple[str, list[dict[str, Any]]]:
            self.calls.append(("context", locked(db)))
            return "## Remembered\n- likes tea", [{"id": "m1", "scope": "user", "content": "x"}]

        return adapter.MemoryReads(write_policy=policy, context=context)


def patch_keys(monkeypatch: pytest.MonkeyPatch, *, child_key: str | None = "sk-child") -> None:
    async def root_key(*_a: Any, **_k: Any) -> str:
        return "sk-root"

    async def child(*_a: Any, **_k: Any) -> str | None:
        return child_key

    monkeypatch.setattr(conversation_stream_service, "resolve_llm_api_key_for_agent", root_key)
    monkeypatch.setattr(prepared_run_children, "resolve_llm_api_key_for_agent", child)


def resolver(
    seeded: Seeded, tmp_path: Path, memory: Memory | None = None, **request: Any
) -> adapter.PreparedRunResolver:
    values: dict[str, Any] = {
        "context": seeded.graph.context,
        "resolve_config": adapter.chat_config_resolver(seeded.user, seeded.graph.context),
        "run_root_base": tmp_path / "prepared-runs",
        "messages_history": [{"role": "user", "content": "hi"}],
    }
    values.update(request)
    return adapter.PreparedRunResolver(
        adapter.PreparedRunRequest(**values), memory=(memory or Memory()).reads()
    )


async def materialize(
    seeded: Seeded,
    prepare: adapter.PreparedRunResolver,
    scripts: dict[str, list[AIMessage]],
    server: kit.FakeMcpServer | None = None,
    factory: kit.ScriptedFactory | None = None,
) -> tuple[PreparedRunLease, kit.ScriptedFactory]:
    factory = factory or kit.ScriptedFactory(scripts)

    async def schedule(_lease: Any) -> None:
        assert kit.SENDS == []

    lease = await agent_execution_runtime.materialize_prepared_execution(
        seeded.request,
        prepare_inputs=prepare,
        schedule=schedule,
        seams=kit.seams(factory, server),
    )
    return lease, factory


@dataclass
class Activated:
    seeded: Seeded
    lease: PreparedRunLease
    factory: kit.ScriptedFactory
    prepare: adapter.PreparedRunResolver


async def activate(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripts: Callable[[Seeded], dict[str, list[AIMessage]]],
    factory: Callable[[dict[str, list[AIMessage]]], kit.ScriptedFactory] = kit.ScriptedFactory,
) -> Activated:
    """Seed rows, then materialize + activate through the adapter (no dispatch yet)."""

    kit.fresh_registry(monkeypatch)
    patch_keys(monkeypatch)
    seeded = await seed(db)
    prepare = resolver(seeded, tmp_path)
    built = factory(scripts(seeded))
    lease, used = await materialize(seeded, prepare, {}, factory=built)
    return Activated(seeded, lease, used, prepare)


class CountingSessions:
    """Session factory that tracks how many authority sessions are open."""

    def __init__(self, inner: Callable[[], Any]) -> None:
        self._inner = inner
        self.open = 0

    def __call__(self) -> Any:
        return _Counted(self, self._inner())


class _Counted:
    def __init__(self, owner: CountingSessions, session: Any) -> None:
        self._owner, self._session = owner, session

    async def __aenter__(self) -> Any:
        entered = await self._session.__aenter__()
        self._owner.open += 1
        return entered

    async def __aexit__(self, *exc: Any) -> Any:
        self._owner.open -= 1
        return await self._session.__aexit__(*exc)
