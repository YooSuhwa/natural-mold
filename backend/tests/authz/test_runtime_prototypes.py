"""Lock native middleware ordering and auth interrupt resume behavior before P5."""

import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.authz import runtime_prototype as proto


def scripted_calls() -> list[BaseMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "allowed_action", "args": {}, "id": "allowed-call"},
                {"name": "blocked_action", "args": {}, "id": "blocked-call"},
            ],
        ),
        AIMessage(content="finished"),
    ]


async def test_partial_denial_skips_only_blocked_tool_when_after_model_updates_state() -> None:
    # Given: one model message requests two tools, one forbidden.
    calls: list[str] = []

    @tool
    def allowed_action() -> str:
        """Allowed test action."""
        calls.append("allowed")
        return "ok"

    @tool
    def blocked_action() -> str:
        """Forbidden test action."""
        calls.append("blocked")
        return "unexpected"

    guard = proto.ConnectionGuard(blocked=frozenset({"blocked_action"}))
    graph = create_deep_agent(
        model=proto.ScriptedModel(responses=scripted_calls()),
        tools=[allowed_action, blocked_action],
        middleware=[guard],
        checkpointer=InMemorySaver(),
    )
    # When: the real graph applies aafter_model ToolMessage updates.
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="act")]}, {"configurable": {"thread_id": "partial"}}
    )
    # Then: the permitted tool runs once; the denied tool does not run.
    assert calls == ["allowed"]
    denied = [
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "blocked-call"
    ]
    assert len(denied) == 1
    assert denied[0].status == "error"


@pytest.mark.parametrize("message", ["retry", "skip", "cancel"])
async def test_auth_interrupt_persists_and_resumes_without_duplicate_tools(message: str) -> None:
    # Given: two authenticated tools in one model response; credentials are not ready.
    calls: list[str] = []

    @tool
    def allowed_action() -> str:
        """First authenticated test action."""
        calls.append("allowed")
        return "ok"

    @tool
    def blocked_action() -> str:
        """Second authenticated test action."""
        calls.append("second")
        return "ok"

    guard = proto.ConnectionGuard(
        auth_tools=frozenset({"allowed_action", "blocked_action"}), ready=False
    )
    saver = InMemorySaver()
    config: RunnableConfig = {"configurable": {"thread_id": f"auth-{message}"}}
    graph = create_deep_agent(
        model=proto.ScriptedModel(responses=scripted_calls()),
        tools=[allowed_action, blocked_action],
        middleware=[guard],
        checkpointer=saver,
    )
    first = await graph.ainvoke({"messages": [HumanMessage(content="act")]}, config)
    assert calls == []
    initial = first["__interrupt__"][0]
    snapshot = await graph.aget_state(config)
    assert snapshot.tasks[0].interrupts[0].id == initial.id
    assert initial.value["type"] == "auth_required"
    # When: the user chooses the native respond decision on the same persisted thread.
    guard.ready = message == "retry"
    result = await graph.ainvoke(Command(resume={"type": "respond", "message": message}), config)
    # Then: retry invokes each tool once; skip/cancel invokes neither.
    assert "__interrupt__" not in result
    assert sorted(calls) == (["allowed", "second"] if message == "retry" else [])
    resolved = {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)}
    assert resolved == {"allowed-call", "blocked-call"}


async def test_auth_guard_runs_before_approval_when_native_hitl_is_composed() -> None:
    # Given: the same tool needs a connection and approval.
    @tool
    def allowed_action() -> str:
        """An authenticated action requiring approval."""
        return "ok"

    guard = proto.ConnectionGuard(auth_tools=frozenset({"allowed_action"}), ready=False)
    hitl = HumanInTheLoopMiddleware(interrupt_on={"allowed_action": True})
    graph = create_deep_agent(
        model=proto.ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "allowed_action", "args": {}, "id": "approved-call"}],
                ),
                AIMessage(content="finished"),
            ]
        ),
        tools=[allowed_action],
        # Separate nodes are essential: the guard consumes its own resume value
        # before the approval node starts with an independent interrupt index.
        interrupt_on=None,
        middleware=[hitl, guard],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "guard-before-approval"}}
    # When: the native HITL middleware runs.
    first = await graph.ainvoke({"messages": [HumanMessage(content="act")]}, config)
    # Then: authentication is requested before approval.
    assert first["__interrupt__"][0].value["type"] == "auth_required"
    guard.ready = True
    second = await graph.ainvoke(Command(resume={"type": "respond", "message": "retry"}), config)
    assert second["__interrupt__"][0].value["action_requests"][0]["name"] == "allowed_action"
    final = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert "__interrupt__" not in final


async def test_partial_denial_retains_approval_for_the_other_tool() -> None:
    calls: list[str] = []

    @tool
    def allowed_action() -> str:
        """An allowed action requiring approval."""
        calls.append("allowed")
        return "ok"

    @tool
    def blocked_action() -> str:
        """A denied action which must never execute."""
        calls.append("blocked")
        return "unexpected"

    policies: dict[str, bool | InterruptOnConfig] = {
        name: {"allowed_decisions": ["approve", "reject"], "when": proto.approval_pending}
        for name in ("allowed_action", "blocked_action")
    }
    # Given: both tools normally need approval, but one has already been denied.
    hitl = HumanInTheLoopMiddleware(interrupt_on=policies)
    guard = proto.ConnectionGuard(blocked=frozenset({"blocked_action"}))
    graph = create_deep_agent(
        model=proto.ScriptedModel(responses=scripted_calls()),
        tools=[allowed_action, blocked_action],
        middleware=[hitl, guard],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "partial-approval"}}
    first = await graph.ainvoke({"messages": [HumanMessage(content="act")]}, config)
    # When: guard results enter the native approval node.
    actions = first["__interrupt__"][0].value["action_requests"]
    # Then: the denied tool is absent, and the allowed tool has not executed yet.
    assert [action["name"] for action in actions] == ["allowed_action"]
    assert calls == []
    await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert calls == ["allowed"]


async def test_retry_while_connection_is_still_missing_reinterrupts() -> None:
    @tool
    def action() -> str:
        """Fixture requiring a connection."""
        return "ok"

    guard = proto.ConnectionGuard(auth_tools=frozenset({"action"}), ready=False)
    graph = create_deep_agent(
        model=proto.ScriptedModel(
            responses=[
                AIMessage(
                    content="", tool_calls=[{"name": "action", "args": {}, "id": "retry-call"}]
                ),
                AIMessage(content="finished"),
            ]
        ),
        tools=[action],
        middleware=[guard],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "still-missing"}}
    first = await graph.ainvoke({"messages": [HumanMessage(content="act")]}, config)
    again = await graph.ainvoke(Command(resume={"type": "respond", "message": "retry"}), config)
    assert again["__interrupt__"][0].value == first["__interrupt__"][0].value
    guard.ready = True
    final = await graph.ainvoke(Command(resume={"type": "respond", "message": "retry"}), config)
    assert "__interrupt__" not in final
