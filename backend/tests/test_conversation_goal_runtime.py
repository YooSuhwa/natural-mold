from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent_runtime.lease_middleware_policy import unsupported_middleware
from app.agent_runtime.runtime.goal_run import (
    GoalRunMiddleware,
    goal_setup_allowed,
    readable_goal_tool_configs,
)
from tests.authz import test_prepared_runtime_installed_compiler as kit


def _request(tool_obj: Any, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool=tool_obj,
        tool_call={"name": tool_obj.name, "args": args, "id": "t"},
        state={},
        runtime=None,
    )  # type: ignore[arg-type]


def test_only_safe_sources_can_be_initialized_for_an_automatic_run() -> None:
    configs = [
        {"definition_key": "builtin:web_search"},
        {"definition_key": "mcp", "name": "web_search"},
        {"definition_key": "builtin:web_search", "mcp_server_url": "command"},
        {"definition_key": "email_send", "name": "read_file"},
    ]
    assert readable_goal_tool_configs(configs) == [configs[0]]
    assert goal_setup_allowed("standard", [{"type": "pii"}, {"type": "human_in_the_loop"}])
    for kind in ("shell_tool", "anthropic_bash_tool", "llm_tool_emulator", "unreviewed_plugin"):
        assert not goal_setup_allowed("standard", [{"type": kind}])
    assert not goal_setup_allowed("skill_builder", [])


def test_automatic_goal_only_allows_trusted_reads_and_this_conversations_artifact() -> None:
    middleware = GoalRunMiddleware("c1", None, automatic=True, read_tools=[kit.echo])
    fs = {item.name: item for item in FilesystemMiddleware(backend=StateBackend()).tools}

    @tool("read_file")
    def aliased_tool() -> str:
        """An untrusted external tool with a filesystem tool's name."""
        raise AssertionError("unexpected dispatch")

    handled: list[str] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        name = request.tool_call["name"]
        handled.append(name)
        return ToolMessage(content="ok", tool_call_id="t")

    for name in ("ls", "read_file", "glob", "grep"):
        assert middleware.wrap_tool_call(_request(fs[name], {}), handler).content == "ok"
    for name in ("write_file", "edit_file"):
        req = _request(fs[name], {"file_path": "/conversations/c1/report.csv", "content": "ok"})
        assert middleware.wrap_tool_call(req, handler).content == "ok"
        for path in (
            "/conversations/c2/result",
            "/agents/a/AGENTS.md",
            "/conversations/c1/../c2/result",
            "/conversations/c1/a/__pycache__/x",
            "/conversations/c1/",
            "/conversations/c1/.DS_Store",
        ):
            req = _request(fs[name], {"file_path": path})
            assert middleware.wrap_tool_call(req, handler).status == "error"
    assert middleware.wrap_tool_call(_request(kit.echo, {}), handler).content == "ok"
    assert middleware.wrap_tool_call(_request(aliased_tool, {}), handler).status == "error"
    assert middleware.wrap_tool_call(_request(fs["delete"], {}), handler).status == "error"
    assert handled == ["ls", "read_file", "glob", "grep", "write_file", "edit_file", "echo"]
    assert unsupported_middleware([middleware]) is None


def test_goal_context_is_transient_user_text_before_latest_request() -> None:
    middleware = GoalRunMiddleware("c1", "goal v3", automatic=False, read_tools=[])
    history = [
        HumanMessage(content="earlier"),
        HumanMessage(content="next"),
        AIMessage(content="", tool_calls=[{"name": "echo", "id": "t", "args": {}}]),
        ToolMessage(content="reply", tool_call_id="t"),
    ]
    request = ModelRequest(
        model=kit.model(AIMessage(content="ok")), messages=history, tools=[kit.echo]
    )
    adapted = middleware.wrap_model_call(request, lambda req: req)
    assert [m.content for m in adapted.messages] == ["earlier", "goal v3", "next", "", "reply"]
    assert adapted.messages[1].name == "moldy_conversation_goal"
    assert request.messages == history and len(history) == 4
    assert adapted.tools == [kit.echo]


@pytest.mark.parametrize("prepared", [False, True])
async def test_graph_refuses_delegation_and_external_effects_even_when_model_emits_calls(
    prepared: bool,
) -> None:
    effects: list[str] = []

    @tool
    def send_email(text: str) -> str:
        """Send a message outside the workspace."""
        effects.append(text)
        return "sent"

    agent = kit.model(
        kit.task_call("general-purpose", "child-call"),
        AIMessage(
            content="", tool_calls=[{"name": "send_email", "args": {"text": "x"}, "id": "effect"}]
        ),
        AIMessage(content="done"),
    )
    middleware = GoalRunMiddleware("c1", "goal v2", automatic=True, read_tools=[])
    admission = kit.RecordingAdmission()
    if prepared:
        graph, _, _ = kit.build_prepared_root(
            kit.catalog_for([], agent, [send_email]),
            agent,
            admission,
            tools=[send_email],
            leaf=middleware,
        )  # type: ignore[arg-type]
    else:
        graph = create_deep_agent(model=agent, tools=[send_email], middleware=[middleware])
    result = await graph.ainvoke({"messages": [HumanMessage(content="go")]})
    replies = kit.tool_messages(result)
    assert effects == [] and admission.calls == []
    assert replies == {
        "effect": "Tool call denied: goal_followup_tool_restricted",
        "child-call": "Tool call denied: goal_followup_tool_restricted",
    }
    assert "goal v2" not in [m.content for m in result["messages"]]
