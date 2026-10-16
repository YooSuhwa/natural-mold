"""Prepared children get the installed per-child stack and nested-dispatch isolation.

Parity is checked against the processed child specs the installed
``create_deep_agent`` hands to ``create_sub_agent`` (captured in the test only).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware import subagents as installed_subagents
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY as FORMAT_KEY
from langchain_core.messages import AIMessage

from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.prepared_child_launch import (
    ChildLaunchKey,
    ChildLaunchLedger,
    ChildLaunchState,
)
from app.agent_runtime.prepared_subagent_catalog import (
    PreparedChildPolicy,
    build_prepared_child_catalog,
    model_identity,
    permission_identity,
)
from app.agent_runtime.prepared_task_middleware import precompile_prepared_children
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from app.agent_runtime.runtime_effective_capabilities import EffectiveCapabilityCensus
from tests.authz import prepared_run_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import (
    Findings,
    RecordingAdmission,
    build_prepared_root,
    echo,
    model,
    task_call,
    tool_messages,
)
from tests.conftest import TestSession

PERMS = [FilesystemPermission(operations=["write"], paths=["/locked/**"], mode="interrupt")]
INTERRUPT: dict[str, Any] = {"echo": True}
SKILLS = ["/skills/"]


def _shape(spec: dict[str, Any]) -> tuple[Any, ...]:
    names = [type(m).__name__ for m in spec["middleware"]]
    tools = sorted(t.name for t in spec["tools"])
    return names, tools, sorted(spec.get("interrupt_on") or {}), spec["system_prompt"]


def _compiled_tools(runnable: Any) -> list[str]:
    return sorted(runnable.nodes["tools"].bound.tools_by_name)


def test_children_match_the_installed_stack_and_compiled_tools(monkeypatch) -> None:
    root_model = model(AIMessage(content="done"))
    researcher: Any = {"name": "researcher", "description": "r", "system_prompt": "s"}
    stock: dict[str, Any] = {}
    original = installed_subagents.create_sub_agent

    def capture(spec: Any, **kw: Any) -> Any:
        stock[spec["name"]] = (spec, original(spec, **kw))
        return stock[spec["name"]][1]

    monkeypatch.setattr(installed_subagents, "create_sub_agent", capture)
    create_deep_agent(
        model=root_model,
        tools=[echo],
        subagents=[researcher],
        interrupt_on=INTERRUPT,
        permissions=PERMS,
        skills=SKILLS,
    )
    monkeypatch.setattr(installed_subagents, "create_sub_agent", original)
    policy = {
        "root/researcher": PreparedChildPolicy(
            model_identity(root_model), frozenset({"echo"}), permission_identity(PERMS)
        )
    }
    catalog = build_prepared_child_catalog(
        [dict(researcher)], model=root_model, tools=[echo], permissions=PERMS, stored_policy=policy
    )
    prepared: dict[str, Any] = {}

    def compile_child(spec: Any, **kw: Any) -> Any:
        prepared[spec["name"]] = spec
        return original(spec, **kw)

    runnables = precompile_prepared_children(
        catalog,
        backend=StateBackend(),
        projector=PreparedStateProjector(),
        admission_factory=RecordingAdmission,
        compile_child=compile_child,
        interrupt_on=INTERRUPT,
        skills=SKILLS,
    )
    assert set(prepared) == set(stock) == {"general-purpose", "researcher"}
    for name, (stock_spec, stock_runnable) in stock.items():
        assert _shape(prepared[name]) == _shape(stock_spec)
        prepared_runnable = runnables[(f"root/{name}", "default")]
        assert _compiled_tools(prepared_runnable) == _compiled_tools(stock_runnable)
    assert "write_file" in _compiled_tools(runnables[("root/general-purpose", "default")])
    assert set(prepared["general-purpose"]["interrupt_on"]) >= {"echo", "write_file"}


def _researcher(researcher_model: Any, **extra: Any) -> dict[str, Any]:
    checker = {
        "name": "checker",
        "description": "c",
        "system_prompt": "s",
        "model": model(AIMessage(content="checked")),
        "tools": [],
    }
    spec = {"name": "researcher", "description": "r", "system_prompt": "s", "tools": []}
    return {**spec, "model": researcher_model, "subagents": [checker], **extra}


def _nested_catalog(spec: dict[str, Any], root_model: Any):
    identity = model_identity(spec["model"])
    policy = {
        "root/researcher": PreparedChildPolicy(identity, frozenset()),
        "root/researcher/checker": PreparedChildPolicy(identity, frozenset()),
    }
    return build_prepared_child_catalog(
        [spec], model=root_model, tools=[], permissions=None, stored_policy=policy
    )


@pytest.mark.asyncio
async def test_interceptor_precedes_a_nested_lease_leaf_guard() -> None:
    census = EffectiveCapabilityCensus("root/researcher", (), frozenset(), {})
    guard = LeaseBoundToolMiddleware(
        kit.lease(kit.binding(), "token"),
        census,
        session_factory=TestSession,  # type: ignore[arg-type]
    )
    researcher_model = model(task_call("checker", "call-n"), AIMessage(content="researched"))
    spec = _researcher(researcher_model, middleware=[guard])
    root_model = model(task_call("researcher", "call-r"), AIMessage(content="done"))
    admission = RecordingAdmission()
    graph, _, _ = build_prepared_root(
        _nested_catalog(spec, root_model), root_model, admission, tools=[]
    )
    result = await graph.ainvoke({"messages": [("user", "hi")]})
    assert guard.denials == []
    assert tool_messages(result) == {"call-r": "researched"}
    assert [call[1] for call in admission.calls] == ["root/researcher", "root/researcher/checker"]


@pytest.mark.asyncio
async def test_parent_response_format_does_not_leak_into_nested_dispatch() -> None:
    findings = {"name": "Findings", "args": {"summary": "S"}, "id": "so-1"}
    researcher_model = model(
        task_call("checker", "call-n"), AIMessage(content="", tool_calls=[findings])
    )
    spec = _researcher(researcher_model, _moldy_response_variants=[Findings])
    root_model = model(task_call("researcher", "call-r"), AIMessage(content="done"))
    graph, _, _ = build_prepared_root(
        _nested_catalog(spec, root_model), root_model, RecordingAdmission(), tools=[]
    )
    result = await graph.ainvoke(
        {"messages": [("user", "hi")]}, {"configurable": {FORMAT_KEY: Findings}}
    )
    assert tool_messages(result)["call-r"] == '{"summary":"S"}'


@pytest.mark.asyncio
async def test_parent_task_id_follows_the_invoking_chain() -> None:
    researcher_model = model(
        task_call("checker", "call-n"),
        AIMessage(content="researched"),
        task_call("checker", "call-n"),
        AIMessage(content="researched"),
    )
    spec = _researcher(researcher_model)
    spec["subagents"][0]["model"] = model(AIMessage(content="ok"), AIMessage(content="ok"))
    root_model = model(
        task_call("researcher", "r1"), task_call("researcher", "r2"), AIMessage(content="done")
    )
    admission = RecordingAdmission()
    graph, _, _ = build_prepared_root(
        _nested_catalog(spec, root_model), root_model, admission, tools=[]
    )
    result = await graph.ainvoke({"messages": [("user", "hi")]})
    assert tool_messages(result) == {"r1": "researched", "r2": "researched"}
    assert admission.calls == [
        ("root", "root/researcher", "r1"),
        ("root/2:r1", "root/researcher/checker", "call-n"),
        ("root", "root/researcher", "r2"),
        ("root/2:r2", "root/researcher/checker", "call-n"),
    ]


@pytest.mark.asyncio
async def test_ledger_terminalization_purges_the_runs_slots() -> None:
    ledger, lease_id = ChildLaunchLedger(), uuid.uuid4()
    done, other = (
        ChildLaunchKey(lease_id, "root", "root/x", "a"),
        ChildLaunchKey(uuid.uuid4(), "root", "root/x", "a"),
    )
    assert ledger.claim(done) and ledger.claim(other)
    task = ledger.launch(done, lambda: asyncio.sleep(0))
    await task
    assert ledger.finish(done) and ledger.state(done) is ChildLaunchState.FINISHED
    assert ledger.terminalize(lease_id) == 0
    assert ledger.state(done) is None
    assert ledger.state(other) is ChildLaunchState.RESERVED
