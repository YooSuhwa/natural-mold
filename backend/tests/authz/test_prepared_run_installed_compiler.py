"""The materialized lease graphs, inspected and driven through the installed compiler.

Root and nested children are compiled by ``materialize_and_activate`` with
real Deep Agents/LangChain compilers and scripted lease models (every send
under a dispatch permit against real rows). Wrapper order is recorded by
spying the installed and app middleware classes before materialization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents import graph as deepagents_graph
from deepagents.middleware import subagents as installed_subagents
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.subagents import SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY as FORMAT_KEY
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolNode
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_compile, prepared_task_middleware
from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_registry import LEASE_MISSING, get_prepared_run_registry
from app.agent_runtime.prepared_subagent_catalog import response_variant_id
from app.agent_runtime.prepared_task_middleware import PreparedTaskDispatchMiddleware
from app.agent_runtime.runtime_hydration_guard import post_marker_execution
from app.models.conversation_run import ConversationRun
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import Findings, Other, PostMarkerSpies
from tests.conftest import TestSession


def _spy_wrappers(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, str]], list[Any]]:
    entries: list[tuple[str, str]] = []
    stock_children: list[Any] = []
    labels = {
        FilesystemMiddleware: lambda _m: "fs",
        PreparedTaskDispatchMiddleware: lambda m: f"task_mw:{m._parent_path}",
        LeaseBoundToolMiddleware: lambda m: f"leaf:{m._census.participant}",
    }
    for cls, label in labels.items():
        original = cls.awrap_tool_call

        async def recorded(self, request, handler, _original=original, _label=label):
            entries.append((_label(self), request.tool_call["name"]))
            return await _original(self, request, handler)

        monkeypatch.setattr(cls, "awrap_tool_call", recorded)
    execute = ToolNode._execute_tool_async

    async def stock(node: ToolNode, request: Any, *args: Any) -> Any:
        entries.append(("stock", request.tool_call["name"]))
        return await execute(node, request, *args)

    monkeypatch.setattr(ToolNode, "_execute_tool_async", stock)
    init = SubAgentMiddleware.__init__

    def capture(self, *args: Any, **kwargs: Any) -> None:
        stock_children.append(list(kwargs.get("subagents") or ()))
        init(self, *args, **kwargs)

    monkeypatch.setattr(SubAgentMiddleware, "__init__", capture)
    return entries, stock_children


NESTED_SCRIPTS = {
    "root": [kit.echo_call("r-echo"), kit.task_call("researcher", "r-task"), AIMessage("done")],
    "researcher": [
        kit.echo_call("c-echo"),
        kit.task_call("checker", "c-task"),
        AIMessage("researched"),
    ],
    "checker": [kit.echo_call("k-echo"), AIMessage("checked")],
}


async def _nested(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[tuple[str, str]], list[Any]]:
    entries, stock_children = _spy_wrappers(monkeypatch)
    checker = kit.child("checker", tools=[kit.echo], model=kit.spec("checker"))
    researcher = kit.child(
        "researcher", tools=[kit.echo], model=kit.spec("researcher"), subagents=[checker]
    )
    lease, _factory, queued = await kit.materialize(
        db,
        tmp_path,
        monkeypatch,
        NESTED_SCRIPTS,
        (researcher,),
        {"root/researcher": frozenset({"echo"}), "root/researcher/checker": frozenset({"echo"})},
    )
    entries.clear()
    spies = PostMarkerSpies(monkeypatch)
    with post_marker_execution(str(queued.run_id)):
        result = await lease.runnable.ainvoke(
            {"messages": list(lease.messages)}, dict(lease.config)
        )
    assert result["messages"][-1].content == "done"
    assert spies.compiled == []
    return lease, entries, stock_children


def _task_counts(graph: Any) -> int:
    return list(graph.nodes["tools"].bound.tools_by_name).count("task")


@pytest.mark.asyncio
async def test_installed_core_wrapper_delegates_then_app_interceptor_order_for_root_and_nested_graphs(  # noqa: E501
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease, entries, stock_children = await _nested(db, tmp_path, monkeypatch)

    assert entries == [
        ("fs", "echo"),
        ("task_mw:root", "echo"),
        ("leaf:root", "echo"),
        ("stock", "echo"),
        ("fs", "task"),
        ("task_mw:root", "task"),
        ("fs", "echo"),
        ("task_mw:root/researcher", "echo"),
        ("leaf:root/researcher", "echo"),
        ("stock", "echo"),
        ("fs", "task"),
        ("task_mw:root/researcher", "task"),
        ("fs", "echo"),
        ("leaf:root/researcher/checker", "echo"),
        ("stock", "echo"),
    ]
    runnables = lease.child_runnables
    assert _task_counts(lease.runnable) == 1
    assert _task_counts(runnables[("root/researcher", "default")]) == 1
    assert _task_counts(runnables[("root/researcher/checker", "default")]) == 0
    assert stock_children and all("runnable" in spec for level in stock_children for spec in level)
    assert any("general-purpose" in {s["name"] for s in level} for level in stock_children)


@pytest.mark.asyncio
async def test_task_short_circuits_generic_and_stock_for_root_and_nested_graphs(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lease, entries, _ = await _nested(db, tmp_path, monkeypatch)

    task_entries = [label for label, name in entries if name == "task"]
    assert task_entries == ["fs", "task_mw:root", "fs", "task_mw:root/researcher"]
    assert sorted(set(kit.SENDS)) == ["checker", "researcher", "root"]


@pytest.mark.asyncio
async def test_non_task_delegates_once_to_generic_leaf_for_root_and_nested_graphs(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lease, entries, _ = await _nested(db, tmp_path, monkeypatch)

    echo_entries = [label for label, name in entries if name == "echo"]
    leaves = [label for label in echo_entries if label.startswith("leaf:")]
    assert leaves == ["leaf:root", "leaf:root/researcher", "leaf:root/researcher/checker"]
    assert echo_entries.count("stock") == 3 and echo_entries.count("fs") == 3
    assert kit.ECHOED == ["r-echo", "c-echo", "k-echo"]


def _variant_child(variants: list[Any]) -> dict[str, Any]:
    return kit.child(
        "researcher", tools=[], model=kit.spec("researcher"), _moldy_response_variants=variants
    )


FINDINGS = AIMessage(
    content="", tool_calls=[{"name": "Findings", "args": {"summary": "S"}, "id": "so-1"}]
)


@pytest.mark.asyncio
async def test_dynamic_response_variant_uses_precompiled_child_without_stock_handler(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = {"root": [kit.task_call("researcher", "v-task"), AIMessage("done")]}
    scripts["researcher"] = [FINDINGS]
    lease, _factory, queued = await kit.materialize(
        db,
        tmp_path,
        monkeypatch,
        scripts,
        (_variant_child([Findings]),),
        {"root/researcher": frozenset()},
    )
    assert ("root/researcher", response_variant_id(Findings)) in lease.child_runnables
    spies = PostMarkerSpies(monkeypatch)
    config = {"configurable": {**lease.config["configurable"], FORMAT_KEY: Findings}}
    with post_marker_execution(str(queued.run_id)):
        result = await lease.runnable.ainvoke({"messages": list(lease.messages)}, config)

    by_id = {m.tool_call_id: m.content for m in result["messages"] if isinstance(m, ToolMessage)}
    assert by_id == {"v-task": '{"summary":"S"}'}
    assert spies.compiled == [] and "task" not in spies.executed


async def _async_format() -> type:
    return Findings


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [lambda: Findings, _async_format, object()])
async def test_raw_callable_async_or_unknown_variant_fails_before_marker(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    scripts = {"root": [AIMessage("done")], "researcher": [AIMessage("r")]}
    with pytest.raises(ValueError, match="response_variant_unsupported"):
        await kit.materialize(
            db,
            tmp_path,
            monkeypatch,
            scripts,
            (_variant_child([bad]),),
            {"root/researcher": frozenset()},
        )
    async with TestSession() as check:
        rows = (await check.execute(ConversationRun.__table__.select())).all()
    assert all(row.status == "queued" and row.runtime_materialized_at is None for row in rows)
    assert get_prepared_run_registry().active_run_ids() == set()
    assert kit.SENDS == []


@pytest.mark.asyncio
async def test_post_marker_stock_handler_or_compile_is_forbidden(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = [kit.task_call("researcher", f"x-task-{index}") for index in range(4)]
    scripts = {"root": root, "researcher": [FINDINGS]}
    lease, _factory, queued = await kit.materialize(
        db,
        tmp_path,
        monkeypatch,
        scripts,
        (_variant_child([Findings]),),
        {"root/researcher": frozenset()},
    )
    forbidden: list[str] = []

    def deny(name: str):
        def counted(*_args: Any, **_kwargs: Any) -> Any:
            forbidden.append(name)
            raise AssertionError(f"post-marker {name}")

        return counted

    monkeypatch.setattr(prepared_run_compile, "create_deep_agent", deny("create_deep_agent"))
    monkeypatch.setattr(deepagents_graph, "create_agent", deny("graph_compile"))
    monkeypatch.setattr(installed_subagents, "create_sub_agent", deny("create_sub_agent"))
    monkeypatch.setattr(prepared_task_middleware, "create_sub_agent", deny("create_sub_agent"))
    spies = PostMarkerSpies(monkeypatch)
    base = dict(lease.config["configurable"])
    for runtime_format in (lambda: Findings, _async_format, Other, "dict:unprepared"):
        config = {"configurable": {**base, FORMAT_KEY: runtime_format}}
        with post_marker_execution(str(queued.run_id)), pytest.raises(DispatchDeniedError):
            await lease.runnable.ainvoke({"messages": list(lease.messages)}, config)
    await get_prepared_run_registry().terminalize(queued.run_id)
    sends = list(kit.SENDS)
    with (
        post_marker_execution(str(queued.run_id)),
        pytest.raises(DispatchDeniedError, match=LEASE_MISSING),
    ):
        await lease.runnable.ainvoke({"messages": list(lease.messages)}, dict(lease.config))

    assert sends == kit.SENDS
    assert forbidden == [] and spies.compiled == [] and "task" not in spies.executed
