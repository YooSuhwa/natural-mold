"""The prepared ``task`` interceptor short-circuits the installed stock handler.

The control characterizes the installed Deep Agents path first; the prepared
path then runs through the actual installed ``create_deep_agent`` graph.
"""

from __future__ import annotations

from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.middleware.subagents import SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY as FORMAT_KEY
from deepagents.middleware.subagents import TaskToolSchema
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import ChildLaunchKey, ChildLaunchState
from app.agent_runtime.prepared_subagent_catalog import response_variant_id
from app.agent_runtime.prepared_task_middleware import PreparedTaskDispatchMiddleware
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from tests.authz.test_prepared_child_launch import CATALOG, setup  # noqa: F401 - fixture
from tests.authz.test_prepared_runtime_installed_compiler import (
    Findings,
    LeafSpy,
    Other,
    PostMarkerSpies,
    RecordingAdmission,
    build_prepared_root,
    catalog_for,
    echo,
    model,
    recording_compile,
    task_call,
    tool_messages,
)

HI: Any = {"messages": [("user", "hi")]}


@pytest.mark.asyncio
async def test_stock_dynamic_response_paths_are_characterized(monkeypatch) -> None:
    compiled_child = create_agent(model(AIMessage(content="child")), tools=[])
    stock = create_deep_agent(
        model=model(task_call("x", "call-1"), AIMessage(content="done")),
        subagents=[{"name": "x", "description": "d", "runnable": compiled_child}],
    )
    with pytest.raises(ValueError, match="cannot be used with compiled subagent"):
        await stock.ainvoke(HI, {"configurable": {FORMAT_KEY: Findings}})

    child_model = model(
        AIMessage(
            content="", tool_calls=[{"name": "Findings", "args": {"summary": "S"}, "id": "so-1"}]
        )
    )
    raw = create_deep_agent(
        model=model(task_call("x", "call-1"), AIMessage(content="done")),
        subagents=[
            {
                "name": "x",
                "description": "d",
                "system_prompt": "s",
                "model": child_model,
                "tools": [],
            }
        ],
    )
    spies = PostMarkerSpies(monkeypatch)
    result = await raw.ainvoke(HI, {"configurable": {FORMAT_KEY: Findings}})
    assert spies.compiled == [Findings]
    assert spies.executed == ["task"]
    assert result["messages"][2].content == '{"summary":"S"}'


@pytest.mark.asyncio
async def test_prepared_task_short_circuits_stock_handler_and_generic_leaf_guard(
    monkeypatch,
) -> None:
    child_model = model(
        AIMessage(
            content="", tool_calls=[{"name": "Findings", "args": {"summary": "S"}, "id": "so-1"}]
        )
    )
    root_model = model(
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "e"}, "id": "call-0"}]),
        task_call("researcher", "call-1"),
        AIMessage(content="done"),
    )
    spec = {
        "name": "researcher",
        "description": "r",
        "system_prompt": "s",
        "model": child_model,
        "tools": [echo],
        "_moldy_response_variants": [Findings],
    }
    catalog = catalog_for([spec], root_model, [echo])
    admission, leaf = RecordingAdmission(), LeafSpy()
    graph, interceptor, runnables = build_prepared_root(
        catalog, root_model, admission, tools=[echo], leaf=leaf
    )
    tools_by_name = graph.nodes["tools"].bound.tools_by_name
    assert interceptor.tools == [] and list(tools_by_name).count("task") == 1
    assert tools_by_name["task"].args_schema is TaskToolSchema
    assert ("root/researcher", response_variant_id(Findings)) in runnables

    spies = PostMarkerSpies(monkeypatch)  # the marker: no compilation from here on
    result = await graph.ainvoke(
        {"messages": [("user", "hi")]}, {"configurable": {FORMAT_KEY: Findings}}
    )

    assert spies.compiled == [] and "task" not in spies.executed and spies.executed == ["echo"]
    assert leaf.names == ["echo"]
    assert [call[2] for call in admission.calls] == ["call-1"]
    tool_message = next(
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "call-1"
    )
    assert tool_message.content == '{"summary":"S"}'


def _request(call_id: str, runtime_id: str | None, args: dict[str, Any]) -> ToolCallRequest:
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=runtime_id,
        store=None,
    )
    call = {"name": "task", "args": args, "id": call_id, "type": "tool_call"}
    return ToolCallRequest(tool_call=call, tool=None, state={"messages": []}, runtime=runtime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unknown_callable_or_async_variant_fails_before_marker(monkeypatch) -> None:
    async def async_format() -> type:
        return Findings

    root_model = model(AIMessage(content="done"))
    for bad in (lambda: Findings, async_format, object()):
        spec = {
            "name": "researcher",
            "description": "r",
            "system_prompt": "s",
            "model": root_model,
            "tools": [],
            "_moldy_response_variants": [bad],
        }
        with pytest.raises(ValueError, match="response_variant_unsupported"):
            catalog_for([spec], root_model, [])

    catalog = catalog_for([], root_model, [])
    admission = RecordingAdmission()
    _graph, interceptor, _ = build_prepared_root(catalog, root_model, admission, tools=[])
    spies = PostMarkerSpies(monkeypatch)
    handler_calls: list[Any] = []

    async def handler(request: ToolCallRequest) -> Any:
        handler_calls.append(request)

    good = {"description": "d", "subagent_type": "general-purpose"}
    with pytest.raises(DispatchDeniedError, match="task_invocation_id_mismatch"):
        await interceptor.awrap_tool_call(_request("a", "b", good), handler)
    invalid = await interceptor.awrap_tool_call(_request("a", "a", {"description": "d"}), handler)
    unknown = await interceptor.awrap_tool_call(
        _request("a", "a", {**good, "subagent_type": "ghost"}), handler
    )
    assert isinstance(invalid, ToolMessage) and isinstance(unknown, ToolMessage)
    assert invalid.status == "error" and "does not exist" in str(unknown.content)
    for runtime_format in (lambda: Findings, Other, "dict:unprepared"):
        request = _request("a", "a", good)
        request.runtime.config["configurable"] = {FORMAT_KEY: runtime_format}
        with pytest.raises(DispatchDeniedError):
            await interceptor.awrap_tool_call(request, handler)
    assert handler_calls == [] and admission.calls == [] and spies.compiled == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [{"type": "object", "properties": {"a": {"type": "string"}}}, Findings, ToolStrategy(Other)],
)
@pytest.mark.parametrize("as_token", [False, True])
async def test_variants_select_the_exact_precompiled_runnable(variant: Any, as_token: bool) -> None:
    invoked: list[str] = []
    root_model = model(task_call("researcher", "call-7"), AIMessage(content="done"))
    spec = {
        "name": "researcher",
        "description": "r",
        "system_prompt": "s",
        "model": root_model,
        "tools": [],
        "_moldy_response_variants": [variant, Findings],
    }
    graph, _interceptor, _ = build_prepared_root(
        catalog_for([spec], root_model, []),
        root_model,
        RecordingAdmission(),
        tools=[],
        compile_child=recording_compile(invoked),
    )
    token = response_variant_id(variant)
    config = {"configurable": {FORMAT_KEY: token if as_token else variant}}
    result = await graph.ainvoke({"messages": [("user", "hi")]}, config)
    assert invoked == [token]
    assert tool_messages(result) == {"call-7": f"variant={token}"}


@pytest.mark.asyncio
async def test_real_admission_launches_once_without_an_open_authority_session(
    setup,  # noqa: F811 - imported fixture
) -> None:
    invoked: list[str] = []
    open_at_child: list[int] = []
    probe = lambda: open_at_child.append(setup["sessions"].open)  # noqa: E731
    root_model = model(
        task_call("researcher", "call-1"),
        AIMessage(content="done"),
        task_call("researcher", "call-1"),
    )
    graph, _interceptor, _ = build_prepared_root(
        CATALOG,
        root_model,
        setup["admission"],
        tools=[],
        compile_child=recording_compile(invoked, probe),
    )
    result = await graph.ainvoke({"messages": [("user", "hi")]})
    assert tool_messages(result) == {"call-1": "variant=default"}
    assert open_at_child == [0] and setup["sessions"].opened == 1
    key = ChildLaunchKey(setup["lease"].binding.run_id, "root", "root/researcher", "call-1")
    assert setup["ledger"].state(key) is ChildLaunchState.FINISHED

    with pytest.raises(DispatchDeniedError) as duplicate:
        await graph.ainvoke({"messages": [("user", "again")]})
    assert duplicate.value.code == "child_launch_duplicate" and invoked == ["default"]


class _FailingProjector(PreparedStateProjector):
    def child_input(self, state: Any, description: str) -> dict[str, Any]:
        raise RuntimeError("projection failed")


@pytest.mark.asyncio
async def test_projection_failure_leaves_no_reserved_slot() -> None:
    root_model = model(AIMessage(content="done"))
    catalog = catalog_for([], root_model, [])
    admission = RecordingAdmission()
    _graph, interceptor, runnables = build_prepared_root(catalog, root_model, admission, tools=[])
    failing = PreparedTaskDispatchMiddleware(
        catalog=catalog,
        parent_path="root",
        runnables=runnables,
        projector=_FailingProjector(),
        admission_factory=lambda: admission,
    )
    good = {"description": "d", "subagent_type": "general-purpose"}
    with pytest.raises(RuntimeError, match="projection failed"):
        await failing.awrap_tool_call(_request("a", "a", good), _never)
    key = ChildLaunchKey(admission.lease_id, "root", "root/general-purpose", "a")
    assert admission.ledger.state(key) is None


async def _never(request: ToolCallRequest) -> Any:
    raise AssertionError("handler reached")
