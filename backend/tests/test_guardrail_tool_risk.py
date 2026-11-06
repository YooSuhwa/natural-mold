"""Company default actions per tool risk level (guardrail-minimum-plan.md C5, step 5).

Agents are built with Moldy's real ``build_agent`` and a fake model that records the tools
it was offered, so each test checks what the model could call, what ran and what stopped
for approval.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.runtime.interrupts import _default_interrupt_on_from_tools
from app.agent_runtime.runtime_agent_builder import build_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.guardrails import tool_risk
from app.guardrails.policy import resolve_tool_risk
from app.guardrails.tool_risk import (
    BLOCKED_MESSAGE,
    DEFAULT_ACTIONS,
    InvalidToolRiskError,
    ToolRiskPolicy,
    actions_from_json,
    interrupts_for,
    policy_from_actions,
)
from app.models.organization import Tenant
from app.services.chat.trigger_blocked_tools import _unsafe
from app.tools.risk import ToolRiskLevel, attach_tool_risk, risk_metadata_dict
from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

POLICY = f"{TENANT}/guardrails"


class ToolRecordingModel(FakeMessagesListChatModel):
    """Answers from ``responses`` and keeps the tool names of every ``bind_tools``."""

    offered: list[list[str]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolRecordingModel:
        names = []
        for item in tools:
            name = getattr(item, "name", None) or (
                item.get("name") if isinstance(item, dict) else None
            )
            names.append(str(name))
        self.offered.append(names)
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        return super()._generate(messages, *args, **kwargs)


def _with_level(target: BaseTool, level: ToolRiskLevel) -> BaseTool:
    return attach_tool_risk(target, risk_metadata_dict(level))


def _tools(calls: list[str]) -> tuple[BaseTool, BaseTool]:
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

    return (
        _with_level(lookup, ToolRiskLevel.READ_ONLY),
        _with_level(send_mail, ToolRiskLevel.EXTERNAL_MUTATION),
    )


def _call(name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {"query": "x", "body": "x"}, "id": "c1", "type": "tool_call"}
        ],
    )


async def _run(
    policy: ToolRiskPolicy | None,
    response: AIMessage,
    tools: list[BaseTool],
    *,
    interrupt: bool = True,
    subagents: list[dict[str, Any]] | None = None,
) -> tuple[ToolRecordingModel, Any, RunnableConfig]:
    model = ToolRecordingModel(responses=[response, AIMessage(content="끝")], offered=[])
    # What the runtime starts from: default approvals from the tools' risk metadata.
    defaults = _default_interrupt_on_from_tools(tools)
    agent = build_agent(
        model,
        tools,
        "You are a test agent.",
        checkpointer=MemorySaver(),
        interrupt_on=defaults if interrupt else None,
        subagents=subagents,
        tool_risk=policy,
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": "해줘"}]}, config)
    return model, agent, config


async def _state(agent: Any, config: RunnableConfig) -> Any:
    return await agent.aget_state(config)


def _policy(**actions_by_level: str) -> ToolRiskPolicy:
    policy = policy_from_actions(actions_by_level)
    assert policy is not None
    return policy


# --- the policy -----------------------------------------------------------------------


def test_defaults_keep_todays_behaviour_and_unknown_names_are_refused() -> None:
    assert DEFAULT_ACTIONS["read_only"] == "allow"
    assert {DEFAULT_ACTIONS[level] for level in DEFAULT_ACTIONS if level != "read_only"} == {
        "approval"
    }
    assert policy_from_actions(dict(DEFAULT_ACTIONS)) is None
    with pytest.raises(InvalidToolRiskError):
        actions_from_json({"dangerous": "block"})
    with pytest.raises(InvalidToolRiskError):
        actions_from_json({"read_only": "maybe"})


def test_allow_drops_only_the_default_approval() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)
    custom = {"allowed_decisions": ["approve"]}
    policy = _policy(external_mutation="allow", write_internal="allow")

    relaxed = interrupts_for(
        {"send_mail": {"allowed_decisions": ["approve", "edit", "reject"]}, "write_file": custom},
        [lookup, send_mail],
        policy,
    )

    # The agent's own approval stays; code execution keeps its default approval.
    assert relaxed == {
        "write_file": custom,
        "execute": {"allowed_decisions": ["approve", "reject"]},
    }


# --- a real agent ---------------------------------------------------------------------


async def test_a_blocked_level_is_never_offered_and_a_call_is_refused() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)

    model, agent, config = await _run(
        _policy(external_mutation="block"), _call("send_mail"), [lookup, send_mail]
    )

    assert all("send_mail" not in offered for offered in model.offered)
    assert "lookup" in model.offered[0]
    assert calls == []
    result = next(
        m for m in (await _state(agent, config)).values["messages"] if isinstance(m, ToolMessage)
    )
    assert (result.status, result.content) == ("error", BLOCKED_MESSAGE)


async def test_a_blocked_deep_agents_tool_is_hidden_too() -> None:
    calls: list[str] = []
    model, _, _ = await _run(
        _policy(write_internal="block", code_execution="block"),
        AIMessage(content="끝"),
        list(_tools(calls)),
    )

    offered = set(model.offered[0])
    assert not offered & {"write_file", "edit_file", "execute"}
    assert "read_file" in offered


async def test_approval_on_a_read_only_tool_stops_for_the_card() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)

    _, agent, config = await _run(
        _policy(read_only="approval"), _call("lookup"), [lookup, send_mail]
    )

    state = await _state(agent, config)
    assert calls == []
    assert state.interrupts, "the read-only call should wait for approval"


async def test_allow_runs_an_external_tool_without_the_card() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)

    _, agent, config = await _run(
        _policy(external_mutation="allow"), _call("send_mail"), [lookup, send_mail]
    )

    assert calls == ["send_mail"]
    assert not (await _state(agent, config)).interrupts


async def test_without_a_company_choice_the_external_tool_still_waits() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)

    _, agent, config = await _run(None, _call("send_mail"), [lookup, send_mail])

    assert calls == []
    assert (await _state(agent, config)).interrupts


async def test_a_declarative_child_loses_the_blocked_tool() -> None:
    calls: list[str] = []
    lookup, send_mail = _tools(calls)
    child_tools: list[BaseTool] = [lookup, send_mail]
    child = {
        "name": "mailer",
        "description": "Sends mail.",
        "system_prompt": "child",
        "tools": child_tools,
        "interrupt_on": {"send_mail": {"allowed_decisions": ["approve", "reject"]}},
    }
    policy = _policy(external_mutation="block")

    applied = tool_risk.apply_to_agent(
        policy, tools=[lookup], interrupt_on={}, middleware=[], subagents=[child]
    )

    [spec] = applied.subagents
    assert [t.name for t in spec["tools"]] == ["lookup"]
    assert "send_mail" not in spec["interrupt_on"]
    assert isinstance(spec["middleware"][-1], tool_risk.ToolRiskMiddleware)
    assert "send_mail" in spec["middleware"][-1].blocked


def test_a_company_blocked_tool_no_longer_stops_a_scheduled_run() -> None:
    configs = [{"name": "http", "definition_key": "http_request"}]
    policy = _policy(external_mutation="block", code_execution="block")

    assert _unsafe(None, configs, has_agent_skills=True)
    assert _unsafe(policy, configs, has_agent_skills=True) == []


# --- company API and resolution -------------------------------------------------------


@pytest.fixture
async def _member(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _put(client: AsyncClient, body: dict[str, Any]) -> Any:
    etag = (await client.get(POLICY)).headers["ETag"]
    return await client.put(POLICY, json=body, headers={"If-Match": etag})


@pytest.mark.usefixtures("_member")
async def test_the_company_saves_actions_per_level_and_keeps_them_when_omitted(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    assert (await client.get(POLICY)).json()["tool_risk"] == DEFAULT_ACTIONS

    saved = await _put(
        client,
        {"mode": "open", "tool_risk": {"external_mutation": "block", "read_only": "approval"}},
    )
    later = await _put(client, {"mode": "off"})  # the 4-2 screen sends no tool_risk

    assert saved.status_code == 200, saved.text
    expected = {**DEFAULT_ACTIONS, "external_mutation": "block", "read_only": "approval"}
    assert saved.json()["tool_risk"] == expected
    assert later.json()["tool_risk"] == expected
    async with TestSession() as check:
        policy = await resolve_tool_risk(check, TEST_TENANT_ID, "standard")
        assert policy is not None and policy.action("external_mutation") == "block"
        assert await resolve_tool_risk(check, TEST_TENANT_ID, "builder") is None
    [first, _] = [e for e in await actions(TEST_TENANT_ID) if e.action == "guardrail.policy_update"]
    assert first.event_metadata is not None
    assert first.event_metadata["after"]["tool_risk"] == expected


@pytest.mark.usefixtures("_member")
async def test_an_unknown_level_or_action_is_rejected(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)

    level = await _put(client, {"mode": "open", "tool_risk": {"dangerous": "block"}})
    action = await _put(client, {"mode": "open", "tool_risk": {"read_only": "maybe"}})

    assert (level.status_code, action.status_code) == (422, 422)


async def test_tool_risk_applies_even_when_detection_is_off(db: AsyncSession) -> None:
    await seed_scope(db)
    async with TestSession() as session:
        tenant = await session.get(Tenant, TEST_TENANT_ID)
        assert tenant is not None
        tenant.settings = {"guardrails": {"mode": "off", "tool_risk": {"unknown": "block"}}}
        await session.commit()

    async with TestSession() as check:
        policy = await resolve_tool_risk(check, TEST_TENANT_ID, "standard")

    assert policy is not None and policy.action(ToolRiskLevel.UNKNOWN) == "block"


async def test_a_run_builds_its_agent_with_the_pinned_tool_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = SimpleNamespace(
        model=object(),
        tools=[],
        system_prompt="p",
        middleware=[],
        interrupt_on=None,
        unattended=None,
        backend=None,
        skills_sources=None,
        memory_sources=None,
        permissions=[],
    )
    built: dict[str, Any] = {}

    async def fake_components(*_args: Any, **_kwargs: Any) -> Any:
        return components

    def fake_build_agent(*_args: Any, **kwargs: Any) -> str:
        built.update(kwargs)
        return "agent"

    monkeypatch.setattr(builder, "_prepare_runtime_components", fake_components)
    monkeypatch.setattr(builder, "build_agent", fake_build_agent)
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: None)
    policy = _policy(code_execution="block")
    cfg = AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="m",
        api_key=None,
        base_url=None,
        system_prompt="p",
        tools_config=[],
        thread_id=str(uuid.uuid4()),
        tool_risk=policy,
    )

    await builder._prepare_agent(cfg, messages_history=[], run_id="run-1")

    assert built["tool_risk"] is policy
