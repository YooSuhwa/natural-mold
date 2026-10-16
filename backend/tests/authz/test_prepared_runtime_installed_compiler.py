"""Prepared child closures compiled and driven through the installed compiler.

Shared helpers for the prepared ``task`` tests live here; the graphs are the
actual installed ``create_deep_agent``/``create_agent`` with scripted models.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware import subagents as installed_subagents
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolNode
from pydantic import BaseModel

from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.deep_agent_factory import DeepAgentFactoryBindings, build_agent_impl
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import (
    ChildLaunchKey,
    ChildLaunchLedger,
    ChildLaunchReservation,
)
from app.agent_runtime.prepared_subagent_catalog import (
    ROOT_PATH,
    PreparedChildCatalog,
    PreparedChildPolicy,
    TrustedCompiledChild,
    build_prepared_child_catalog,
    model_identity,
    permission_identity,
    response_variant_id,
)
from app.agent_runtime.prepared_task_middleware import (
    PreparedTaskDispatchMiddleware,
    compiled_subagent_entries,
    precompile_prepared_children,
)
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from tests.authz.runtime_prototype import ScriptedModel


class Findings(BaseModel):
    summary: str


class Other(BaseModel):
    value: int


@tool
def echo(text: str) -> str:
    """Echo."""
    return text


def task_call(subagent: str, call_id: str) -> AIMessage:
    args = {"description": "do it", "subagent_type": subagent}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": args, "id": call_id}])


def model(*responses: AIMessage) -> ScriptedModel:
    return ScriptedModel(responses=list(responses))


class RecordingAdmission:
    """Real ledger winners; records every reservation's invocation ID."""

    def __init__(self) -> None:
        self.ledger, self.lease_id, self.calls = ChildLaunchLedger(), uuid.uuid4(), []

    async def reserve_child_launch(self, **kw: str) -> ChildLaunchReservation:
        self.calls.append((kw["parent_task_id"], kw["child_id"], kw["invocation_id"]))
        key = ChildLaunchKey(self.lease_id, *self.calls[-1])
        if not self.ledger.claim(key):
            raise DispatchDeniedError("child_launch_duplicate")
        return ChildLaunchReservation(key, self.ledger)


class LeafSpy(AgentMiddleware[Any, Any, Any]):
    """Stands in for the generic leaf guard placed after the interceptor."""

    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable) -> Any:
        self.names.append(request.tool_call["name"])
        return await handler(request)


class PostMarkerSpies:
    """Counts stock compilation and stock tool execution after the marker."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.compiled: list[Any] = []
        self.executed: list[str] = []
        original = ToolNode._execute_tool_async

        def compile_spy(*args: Any, **kwargs: Any) -> Any:
            self.compiled.append(kwargs.get("response_format"))
            return create_agent(*args, **kwargs)

        async def execute_spy(node: ToolNode, request: ToolCallRequest, *a: Any) -> Any:
            self.executed.append(request.tool_call["name"])
            return await original(node, request, *a)

        monkeypatch.setattr(installed_subagents, "create_agent", compile_spy)
        monkeypatch.setattr(ToolNode, "_execute_tool_async", execute_spy)


def catalog_for(specs: list[dict[str, Any]], root_model: Any, tools: list[Any]):
    policy = {
        f"root/{s['name']}": PreparedChildPolicy(
            model_identity(s["model"]),
            frozenset({"echo"}),
            permission_identity(s.get("permissions")),
        )
        for s in specs
    }
    return build_prepared_child_catalog(
        specs, model=root_model, tools=tools, permissions=None, stored_policy=policy
    )


def recording_compile(invoked: list[str], probe: Callable[[], Any] = lambda: None):
    def compile_child(spec: dict[str, Any], *, response_format: Any = None) -> Any:
        variant_id = response_variant_id(response_format)

        async def run(state: dict[str, Any]) -> dict[str, Any]:
            invoked.append(variant_id)
            probe()
            return {"messages": [AIMessage(content=f"variant={variant_id}")]}

        return RunnableLambda(run)

    return compile_child


def build_prepared_root(
    catalog: PreparedChildCatalog,
    root_model: Any,
    admission: Any,
    *,
    tools: list[Any],
    leaf: LeafSpy | None = None,
    compile_child: Callable[..., Any] | None = None,
):
    common: dict[str, Any] = {
        "projector": PreparedStateProjector(),
        "admission_factory": lambda: admission,
    }
    compile_child = compile_child or installed_subagents.create_sub_agent
    runnables = precompile_prepared_children(
        catalog, backend=StateBackend(), compile_child=compile_child, **common
    )
    interceptor = PreparedTaskDispatchMiddleware(
        catalog=catalog, parent_path=ROOT_PATH, runnables=runnables, **common
    )
    bindings = DeepAgentFactoryBindings(
        create_deep_agent=create_deep_agent,  # type: ignore[arg-type]
        with_compatibility=lambda middleware, **_: list(middleware or ()),
        normalize_subagents=lambda *a, **k: pytest.fail("stock normalization reached"),
    )
    graph = build_agent_impl(
        root_model,
        tools,
        "root",
        middleware=[leaf] if leaf else [],
        subagents=compiled_subagent_entries(catalog, runnables),  # type: ignore[arg-type]
        bindings=bindings,
        prepared_task_middleware=interceptor,
    )
    return graph, interceptor, runnables


def tool_messages(result: dict[str, Any]) -> dict[str, Any]:
    return {m.tool_call_id: m.content for m in result["messages"] if isinstance(m, ToolMessage)}


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_source", ["implicit", "legacy", "stored"])
async def test_general_purpose_child_from_each_policy_source(monkeypatch, policy_source) -> None:
    root_model = model(
        task_call("general-purpose", "call-gp"),
        AIMessage(content="gp-answer"),
        AIMessage(content="done"),
    )
    common = {
        "model": root_model,
        "tools": [echo],
        "backend": StateBackend(),
        "permissions": None,
        "interrupt_on": None,
        "skills": None,
    }
    specs: list[dict[str, Any]] = []
    if policy_source == "legacy":
        specs = builder._normalize_declarative_subagents([], **common)
    elif policy_source == "stored":
        specs = builder._normalize_stored_policy_subagents(
            [], filesystem_tool_names=("read_file",), todo_enabled=False, **common
        )
    catalog = catalog_for(specs, root_model, [echo])
    assert list(catalog.children) == ["root/general-purpose"]
    admission = RecordingAdmission()
    graph, _interceptor, _ = build_prepared_root(catalog, root_model, admission, tools=[echo])
    spies = PostMarkerSpies(monkeypatch)
    result = await graph.ainvoke({"messages": [("user", "hi")]})
    assert tool_messages(result) == {"call-gp": "gp-answer"}
    assert admission.calls == [("root", "root/general-purpose", "call-gp")]
    assert spies.compiled == [] and "task" not in spies.executed


@pytest.mark.asyncio
async def test_nested_and_trusted_compiled_children_dispatch_per_level(monkeypatch) -> None:
    checker = {
        "name": "checker",
        "description": "c",
        "system_prompt": "s",
        "model": model(AIMessage(content="checked")),
        "tools": [],
    }
    researcher_model = model(task_call("checker", "call-n"), AIMessage(content="researched"))
    researcher = {
        "name": "researcher",
        "description": "r",
        "system_prompt": "s",
        "model": researcher_model,
        "tools": [],
        "subagents": [checker],
    }
    compiled = create_agent(model(AIMessage(content="compiled-out")), tools=[echo])
    identity = model_identity(researcher_model)
    policy = {
        "root/researcher": PreparedChildPolicy(identity, frozenset()),
        "root/researcher/checker": PreparedChildPolicy(identity, frozenset()),
        "root/auditor": PreparedChildPolicy("trusted:auditor", frozenset({"echo"})),
    }
    trusted = {
        "root/auditor": TrustedCompiledChild(compiled, "trusted:auditor", frozenset({"echo"}))
    }
    root_model = model(
        task_call("researcher", "call-r"), task_call("auditor", "call-a"), AIMessage(content="done")
    )
    catalog = build_prepared_child_catalog(
        [researcher, {"name": "auditor", "description": "a", "runnable": compiled}],
        model=root_model,
        tools=[],
        permissions=None,
        stored_policy=policy,
        trusted_compiled=trusted,
    )
    admission = RecordingAdmission()
    graph, _interceptor, runnables = build_prepared_root(catalog, root_model, admission, tools=[])
    assert runnables[("root/auditor", "default")] is compiled
    spies = PostMarkerSpies(monkeypatch)
    result = await graph.ainvoke({"messages": [("user", "hi")]})
    assert tool_messages(result) == {"call-r": "researched", "call-a": "compiled-out"}
    assert admission.calls == [
        ("root", "root/researcher", "call-r"),
        ("root/6:call-r", "root/researcher/checker", "call-n"),
        ("root", "root/auditor", "call-a"),
    ]
    assert spies.compiled == [] and "task" not in spies.executed
