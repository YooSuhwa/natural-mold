"""Shared fixtures for prepared-run materialization tests (internal I15).

Models are scripted, but every send goes through the lease ``LeaseSendGuard``
(fresh dispatch permit against real DB rows), so provider calls are counted
and fail closed before activation. Graphs are the installed compilers.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool, StructuredTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.agent_runtime.prepared_run_inputs import MaterializerSeams, PreparationInputs
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.prepared_run_transaction import PrepareInputs, materialize_and_activate
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_snapshot_catalog import SnapshotCatalogBuilder
from app.agent_runtime.tool_access_policy import ToolOrigin
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime, conversation_run_service
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from app.services.prepared_run_state import fail_prepared_run
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.authz.runtime_prototype import ScriptedModel
from tests.conftest import TestSession

SENDS: list[str] = []
ECHOED: list[str] = []


@tool
def echo(text: str) -> str:
    """Echo."""
    ECHOED.append(text)
    return text


ECHO_ORIGIN = ToolOrigin("echo", "registry", "tests.echo")


def echo_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": "echo", "args": {"text": call_id}, "id": call_id}]
    )


def task_call(subagent: str, call_id: str) -> AIMessage:
    args = {"description": "do it", "subagent_type": subagent}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": args, "id": call_id}])


class GuardedScriptedModel(ScriptedModel):
    """Scripted responses, but each send opens the lease provider permit."""

    _guard: Any = PrivateAttr(default=None)
    _tag: str = PrivateAttr(default="")

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        async def attempt() -> ChatResult:
            SENDS.append(self._tag)
            return self._generate(messages, stop=stop, **kw)

        return await self._guard.send(attempt)


@dataclass(eq=False)
class FakeOwned:
    model: GuardedScriptedModel
    closes: int = 0

    async def aclose(self) -> None:
        self.closes += 1


@dataclass
class ScriptedFactory:
    """Maps ``LeaseModelSpec.model_name`` to scripted responses."""

    scripts: Mapping[str, Sequence[AIMessage]]
    built: dict[str, FakeOwned] = field(default_factory=dict)

    def __call__(self, spec: LeaseModelSpec, guard: LeaseSendGuard) -> FakeOwned:
        model = GuardedScriptedModel(responses=list(self.scripts[spec.model_name]))
        model._guard, model._tag = guard, spec.model_name
        self.built[spec.model_name] = FakeOwned(model)
        return self.built[spec.model_name]

    def closes(self) -> dict[str, int]:
        return {name: owned.closes for name, owned in self.built.items()}


def spec(name: str) -> LeaseModelSpec:
    return LeaseModelSpec(provider="openai", model_name=name, api_key="sk-test")


@dataclass
class FakeMcpServer:
    names: tuple[str, ...] = ("lookup",)
    discoveries: int = 0
    calls: int = 0

    async def discover(self, _key: str, _config: dict, _interceptors: list) -> list[BaseTool]:
        self.discoveries += 1

        async def call(**_kwargs: Any) -> str:
            self.calls += 1
            return "mcp"

        return [
            StructuredTool.from_function(coroutine=call, name=n, description=n) for n in self.names
        ]


def mcp_config(name: str = "lookup") -> dict[str, Any]:
    return {
        "name": name,
        "mcp_tool_name": name,
        "mcp_server_url": "https://mcp.test/a",
        "mcp_transport_headers": {"Authorization": "Bearer mcp-secret"},
        "connection_id": "conn-1",
    }


def inputs(tmp_path: Path, **overrides: Any) -> PreparationInputs:
    run_root = tmp_path / "runs" / uuid.uuid4().hex
    (run_root / "output").mkdir(parents=True)
    builder = SnapshotCatalogBuilder()
    builder.add_bytes("/agents/a/AGENTS.md", b"frozen memory\n", kind="memory")
    values: dict[str, Any] = {
        "thread_id": "",
        "system_prompt": "root prompt",
        "messages": (("user", "hi"),),
        "model": spec("root"),
        "snapshot_catalog": builder.build(),
        "run_root": run_root,
        "writable_roots": {"/run/output": run_root / "output"},
        "forbidden_root": tmp_path / "data",
        "tools": (echo,),
        "tool_origins": (ECHO_ORIGIN,),
        "versions": {"agent_lifecycle": "1"},
    }
    values.update(overrides)
    return PreparationInputs(**values)


def seams(factory: ScriptedFactory, server: FakeMcpServer | None = None) -> MaterializerSeams:
    server = server or FakeMcpServer()
    return MaterializerSeams(model_factory=factory, mcp_discover=server.discover)


@dataclass(frozen=True)
class QueuedRun:
    graph: GatewayGraph
    run_id: uuid.UUID
    request: RuntimeOwnershipRequest


async def queued_run(db: AsyncSession) -> QueuedRun:
    graph = await seed_graph(db, conversations=1)
    run = ConversationRun(
        conversation_id=graph.conversation_ids[0],
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        source="chat",
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.commit()
    request = RuntimeOwnershipRequest(
        run.id, graph.conversation_ids[0], graph.agent_id, graph.context
    )
    return QueuedRun(graph, run.id, request)


def binding_for(queued: QueuedRun) -> PreparedRunBinding:
    context = queued.graph.context
    return PreparedRunBinding(
        context.tenant_id,
        context.org_id,
        context.user_id,
        queued.graph.agent_id,
        queued.graph.conversation_ids[0],
        queued.run_id,
        str(queued.graph.conversation_ids[0]),
    )


def fresh_registry(monkeypatch: Any) -> PreparedRunRegistry:
    SENDS.clear()
    ECHOED.clear()
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    upstream = InMemorySaver()
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: upstream)
    return registry


def resolver(prepared: PreparationInputs, seen: list[bool] | None = None) -> PrepareInputs:
    """Return ``prepared``; record whether the canonical locks were held when called."""

    async def prepare(db: AsyncSession, run: ConversationRun) -> PreparationInputs:
        if seen is not None:
            proof = live_proof(db)
            seen.append(
                proof is not None
                and proof.cursor.holds(LockClass.RUN, run.id)
                and proof.cursor.holds(LockClass.CONVERSATION, run.conversation_id)
            )
        return prepared

    return prepare


def child(name: str, *, tools: list[Any], **extra: Any) -> dict[str, Any]:
    return {"name": name, "description": name, "system_prompt": "s", "tools": tools, **extra}


async def materialize(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripts: dict[str, list[AIMessage]],
    subagents: tuple[dict[str, Any], ...],
    child_tools: dict[str, frozenset[str]],
) -> tuple[Any, ScriptedFactory, QueuedRun]:
    fresh_registry(monkeypatch)
    queued = await queued_run(db)
    factory = ScriptedFactory(scripts)

    async def mark(tx: AsyncSession, run: ConversationRun) -> None:
        await conversation_run_service.transition_run(tx, run, "running")

    async def schedule(_lease: Any) -> None:
        assert SENDS == []

    prepared = inputs(
        tmp_path,
        thread_id=str(queued.graph.conversation_ids[0]),
        subagents=subagents,
        child_tools=child_tools,
    )
    async with TestSession() as tx:
        lease = await materialize_and_activate(
            tx,
            agent_execution_runtime.execution_target(queued.request),
            prepare_inputs=resolver(prepared),
            mark_running=mark,
            fail_running=fail_prepared_run,
            schedule=schedule,
            session_factory=TestSession,
            seams=seams(factory),
        )
    return lease, factory, queued
