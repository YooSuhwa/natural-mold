"""Runs nobody watches: a call needing approval waits for the inbox or is BLOCKED (17.3).

Agents are built with Moldy's real ``build_agent`` and a fake model, as in
``test_guardrail_tool_risk``: each test checks what ran, what was blocked and what paused.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime.runtime.interrupts import _build_interrupt_on_policy
from app.agent_runtime.runtime_agent_builder import build_agent
from app.approvals.unattended import apply_unattended, strip_builtin_defaults
from app.guardrails.tool_risk import policy_from_actions
from app.tools.risk import (
    ToolRiskLevel,
    attach_tool_risk,
    risk_metadata_dict,
    unattended_interrupt_policy,
)
from tests.test_guardrail_tool_risk import ToolRecordingModel


def _tools(calls: list[str]) -> list[BaseTool]:
    @tool
    def lookup(query: str) -> str:
        """Look something up."""
        calls.append("lookup")
        return "found"

    @tool
    def send_mail(body: str) -> str:
        """Send an email."""
        calls.append("send_mail")
        return "sent"

    return [
        attach_tool_risk(lookup, risk_metadata_dict(ToolRiskLevel.READ_ONLY)),
        attach_tool_risk(send_mail, risk_metadata_dict(ToolRiskLevel.EXTERNAL_MUTATION)),
    ]


def _calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {"query": "x", "body": "x"}, "id": f"c{i}", "type": "tool_call"}
            for i, name in enumerate(names)
        ],
    )


def _trigger_policy(tools: list[BaseTool]) -> dict[str, Any]:
    """What ``runtime_preparation`` builds for a run nobody watches."""
    attended = _build_interrupt_on_policy(None, tools, include_ask_user=True, is_trigger_mode=False)
    assert attended is not None
    return unattended_interrupt_policy(attended)


async def _run(
    mode: str | None,
    response: AIMessage,
    tools: list[BaseTool],
    *,
    tool_risk: Any = None,
    subagents: list[dict[str, Any]] | None = None,
) -> tuple[Any, RunnableConfig]:
    model = ToolRecordingModel(responses=[response, AIMessage(content="끝")], offered=[])
    agent = build_agent(
        model,
        tools,
        "You are a test agent.",
        checkpointer=MemorySaver(),
        interrupt_on=_trigger_policy(tools),
        subagents=subagents,
        tool_risk=tool_risk,
        unattended=mode,  # type: ignore[arg-type]
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": "해줘"}]}, config)
    return agent, config


async def _tool_results(agent: Any, config: RunnableConfig) -> dict[str, ToolMessage]:
    state = await agent.aget_state(config)
    return {m.name or "": m for m in state.values["messages"] if isinstance(m, ToolMessage)}


def test_the_unattended_policy_has_nobody_to_ask() -> None:
    policy = _trigger_policy(_tools([]))
    assert "ask_user" not in policy and "send_mail" in policy
    # Deep Agents' own file tools at their default are left to the run (scheduled reports).
    assert strip_builtin_defaults(policy) == {"send_mail": policy["send_mail"]}


@pytest.mark.asyncio
async def test_block_answers_blocked_for_that_call_and_the_rest_of_the_run_goes_on() -> None:
    calls: list[str] = []

    agent, config = await _run("block", _calls("send_mail", "lookup"), _tools(calls))

    assert calls == ["lookup"]
    results = await _tool_results(agent, config)
    blocked = json.loads(str(results["send_mail"].content))
    assert blocked["status"] == "blocked" and blocked["tool"] == "send_mail"
    assert blocked["required_approval"] is True and blocked["reason"]
    assert results["send_mail"].status == "error"
    assert results["lookup"].content == "found"
    state = await agent.aget_state(config)
    assert not state.next  # finished, nothing waits
    assert state.values["messages"][-1].content == "끝"


@pytest.mark.asyncio
async def test_wait_pauses_on_the_standard_approval_interrupt() -> None:
    calls: list[str] = []

    agent, config = await _run("wait", _calls("send_mail"), _tools(calls))

    assert calls == []
    state = await agent.aget_state(config)
    [interrupt] = [i for task in state.tasks for i in task.interrupts]
    assert [a["name"] for a in interrupt.value["action_requests"]] == ["send_mail"]


@pytest.mark.asyncio
async def test_a_company_allow_lets_the_call_run_unattended() -> None:
    calls: list[str] = []
    allow = policy_from_actions({"external_mutation": "allow"})

    await _run("block", _calls("send_mail"), _tools(calls), tool_risk=allow)

    assert calls == ["send_mail"]


@pytest.mark.asyncio
async def test_a_file_write_of_a_scheduled_run_is_not_blocked() -> None:
    write = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/report.md", "content": "# 보고"},
                "id": "w1",
                "type": "tool_call",
            }
        ],
    )

    agent, config = await _run("block", write, _tools([]))

    result = (await _tool_results(agent, config))["write_file"]
    assert "blocked" not in str(result.content)


def test_children_follow_the_mode_with_their_own_policy() -> None:
    child_policy = {"send_mail": {"allowed_decisions": ["approve", "reject"]}}
    root = {"lookup": {"allowed_decisions": ["approve", "reject"]}}
    specs = [
        {"name": "mailer", "description": "x", "system_prompt": "x", "interrupt_on": child_policy},
        {"name": "general-purpose", "description": "x", "system_prompt": "x"},
        {"name": "compiled", "description": "x", "runnable": object()},
    ]

    interrupt_on, middleware, children = apply_unattended(
        "block", interrupt_on=root, middleware=[], subagents=specs
    )

    assert interrupt_on is None
    assert [m.policy for m in middleware] == [root]
    mailer, general, compiled = children
    assert "interrupt_on" not in mailer and mailer["middleware"][-1].policy == child_policy
    assert general["middleware"][-1].policy == root  # inherits the root's approvals
    assert compiled is specs[2]
    waiting, _, kept = apply_unattended("wait", interrupt_on=root, middleware=[], subagents=specs)
    assert waiting == root and kept[0]["interrupt_on"] == child_policy


@pytest.mark.asyncio
async def test_a_conditional_approval_blocks_only_when_it_applies() -> None:
    from app.approvals.unattended import UnattendedApprovalMiddleware

    seen: list[Any] = []

    def when(request: Any) -> bool:
        seen.append(request.tool_call["args"])
        return request.tool_call["args"].get("body") == "주민번호"

    middleware = UnattendedApprovalMiddleware({"send_mail": {"when": when}})

    class Request:
        def __init__(self, body: str) -> None:
            self.tool_call = {"name": "send_mail", "args": {"body": body}, "id": "c"}

    async def handler(_request: Any) -> str:
        return "ran"

    assert await middleware.awrap_tool_call(Request("안녕"), handler) == "ran"
    blocked = await middleware.awrap_tool_call(Request("주민번호"), handler)
    assert isinstance(blocked, ToolMessage) and "blocked" in str(blocked.content)
