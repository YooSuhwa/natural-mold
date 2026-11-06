"""③ Rules that ask the user before a tool runs (guardrail-minimum-plan.md P1).

Moldy's real agent build with a recording fake model; the approval goes through the
standard human-in-the-loop interrupt and a ``Command(resume=...)``, as the chat resume does.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent_runtime.runtime_agent_builder import build_agent
from app.guardrails.approval import approval_entities, with_approvals
from app.guardrails.middleware import Guard
from app.guardrails.rules import Rule, RuleSet
from app.guardrails.templates import InvalidRulesError, rule_from_json
from tests.test_guardrail_middleware import _model

RRN = "900101-1234567"
RULES = RuleSet(
    "company:approval",
    1,
    (Rule("rrn-approval", ("kr",), frozenset({"tool_args"}), "approval", frozenset({"RRN"})),),
)


def _send(calls: list[str]) -> Any:
    @tool
    def send(body: str) -> str:
        """Send a message."""
        calls.append(body)
        return "sent"

    return send


def _call(body: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "send", "args": {"body": body}, "id": "c1", "type": "tool_call"}],
    )


async def _start(body: str, *, interactive: bool = True) -> tuple[Any, RunnableConfig, list[str]]:
    calls: list[str] = []
    model = _model(_call(body), AIMessage(content="끝"))
    agent = build_agent(
        model,
        [_send(calls)],
        "test",
        checkpointer=MemorySaver(),
        interrupt_on={} if interactive else None,
        guard=Guard(RULES, uuid.uuid4(), interactive=interactive),
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": "보내줘"}]}, config)
    return agent, config, calls


async def test_a_value_the_rule_names_stops_for_the_approval_card() -> None:
    agent, config, calls = await _start(f"주민번호 {RRN}")

    state = await agent.aget_state(config)
    assert calls == []
    [interrupt] = state.interrupts
    request = interrupt.value
    assert [a["name"] for a in request["action_requests"]] == ["send"]
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert "주민등록번호" in request["action_requests"][0]["description"]


async def test_approve_runs_the_tool_with_the_value() -> None:
    agent, config, calls = await _start(f"주민번호 {RRN}")

    await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    assert calls == [f"주민번호 {RRN}"]


async def test_reject_never_runs_the_tool() -> None:
    agent, config, calls = await _start(f"주민번호 {RRN}")

    await agent.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "안 돼요"}]}), config
    )

    assert calls == []
    messages = (await agent.aget_state(config)).values["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in messages)


async def test_without_the_value_the_tool_runs_without_asking() -> None:
    agent, config, calls = await _start("안녕하세요")

    assert calls == ["안녕하세요"]
    assert not (await agent.aget_state(config)).interrupts


async def test_a_scheduled_run_refuses_instead_of_waiting() -> None:
    agent, config, calls = await _start(f"주민번호 {RRN}", interactive=False)

    state = await agent.aget_state(config)
    assert calls == [] and not state.interrupts
    result = next(m for m in state.values["messages"] if isinstance(m, ToolMessage))
    assert result.status == "error" and "승인할 수 없는 실행" in str(result.content)


def test_a_tool_that_always_asks_keeps_its_own_entry() -> None:
    calls: list[str] = []
    own = {"allowed_decisions": ["approve", "edit", "reject"]}

    merged = with_approvals({"send": own}, [_send(calls)], RULES)

    assert merged["send"] is own
    assert callable(merged["write_file"]["when"])  # Deep Agents' own tools too


def test_the_answer_is_the_same_for_the_same_arguments() -> None:
    first = approval_entities(RULES, {"body": f"주민번호 {RRN}"})
    again = approval_entities(RULES, {"body": f"주민번호 {RRN}"})

    assert first == again == ["RRN"]


@pytest.mark.parametrize(
    "rule",
    [
        {"stages": ["llm_send"], "packs": ["kr"]},
        {"stages": ["tool_args", "output"], "packs": ["kr"]},
        {"stages": ["tool_args"], "packs": ["kr_model"]},  # a service answer can change
    ],
)
def test_approval_is_for_tool_arguments_with_rule_packs_only(rule: dict[str, Any]) -> None:
    with pytest.raises(InvalidRulesError, match="approval"):
        rule_from_json({"id": "a", "action": "approval", **rule}, 0)


def test_a_valid_approval_rule_loads() -> None:
    rule = rule_from_json(
        {"id": "a", "action": "approval", "stages": ["tool_args"], "packs": ["common", "kr"]}, 0
    )
    assert rule.action == "approval"


def test_an_agent_override_to_approval_follows_the_same_limits() -> None:
    from app.guardrails.policy import apply_overrides

    rules = RuleSet(
        "t",
        1,
        (
            Rule("args", ("kr",), frozenset({"tool_args"}), "warn"),
            Rule("model", ("kr_model",), frozenset({"tool_args"}), "warn"),
        ),
    )
    changed = apply_overrides(
        rules,
        {
            "args": {"enabled": True, "action": "approval"},
            "model": {"enabled": True, "action": "approval"},
        },
        frozenset(),
    )

    assert {r.id: r.action for r in changed.rules} == {"args": "approval", "model": "warn"}
