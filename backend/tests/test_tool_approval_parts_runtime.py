"""Where a run picks up the per-skill and per-child approval rules (tool-governance-plan 3.8).

``tests/test_tool_approval_parts.py`` pins the decisions; this file pins that a real run is
wired to them: the skill tool names its skills, a switched-off skill or child agent is not
part of the run, and the agent factory asks before delegating to a child that asks.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.identity import AgentRunSource, resolve_agent_run_identity
from app.agent_runtime.runtime_agent_builder import build_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import ToolApprovalPolicyV1
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.approvals.tool_modes import ToolApprovalPlan
from app.authz.runtime_access_planner import RuntimeAccessPlan, RuntimeAccessPlanner
from app.credentials.run_subject import subject_for_agent
from app.dependencies import CurrentUser
from app.guardrails.tool_risk import ToolRiskPolicy, actions_from_json, tool_policy_from_json
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.model import Model
from app.tools.keys import agent_ref, part_named, parts_of
from app.tools.risk import ToolRiskLevel, attach_tool_risk, risk_metadata_dict
from tests.agent_runtime.test_subagents_runtime import _agent
from tests.authz.unit_scope_fixture import ensure_unit_scope, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_guardrail_tool_risk import ToolRecordingModel


def _company(**tools: dict[str, Any]) -> ToolRiskPolicy:
    policies = {key: tool_policy_from_json(raw) for key, raw in tools.items()}
    return ToolRiskPolicy(actions_from_json({}), frozenset(), policies)


def _descriptor(tmp_path: Path, slug: str, **values: Any) -> SkillRuntimeDescriptor:
    return SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug=slug,
        name=slug.title(),
        description="x",
        original_storage_path=tmp_path / slug,
        runtime_storage_path=tmp_path / slug,
        **values,
    )


def test_the_skill_tool_names_each_attached_skill_by_its_row(tmp_path: Path) -> None:
    report = _descriptor(tmp_path, "report", approval_default={"mode": "always"})
    notes = _descriptor(tmp_path, "notes")
    ctx = SkillToolContext(
        thread_id="t1",
        tenant_id=uuid.uuid4(),
        output_dir=tmp_path / "out",
        runtime_root=tmp_path,
        descriptors={"report": report, "notes": notes},
        run_id="r1",
    )

    made = _create_skill_execute_tool(ctx)

    parts = parts_of(made)
    assert {slug: ref.key for slug, ref in parts.items()} == {
        "report": f"skill:{report.id}",
        "notes": f"skill:{notes.id}",
    }
    assert (parts["report"].default or {}).get("mode") == "always"
    assert parts["notes"].default is None
    called = part_named("execute_in_skill", {"skill_directory": "/runtime/t1/skills/report/"})
    assert called == "report"


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_a_run_is_prepared_without_the_skills_the_company_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[dict[str, Any]]] = []

    def dependencies(**kwargs: Any) -> list[Any]:
        seen.append(kwargs["agent_skills"])
        raise _Stop

    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [MagicMock()])
    monkeypatch.setattr(builder, "build_skill_dependency_tool_configs", dependencies)
    on, off = {"id": "s-on", "slug": "on"}, {"id": "s-off", "slug": "off"}

    def config(company: ToolRiskPolicy | None) -> AgentConfig:
        return AgentConfig(
            org_context=unit_context(TEST_USER_ID),
            provider="provider",
            model_name="model",
            api_key=None,
            base_url=None,
            system_prompt="x",
            tools_config=[],
            thread_id="t1",
            agent_skills=[on, off],
            tool_risk=company,
        )

    for company in (_company(**{"skill:s-off": {"enabled": False}}), None):
        with pytest.raises(_Stop):
            await builder._prepare_runtime_components(
                config(company),
                is_trigger_mode=False,
                include_ask_user=True,
                include_agent_memory_file=True,
            )

    assert seen == [[on], [on, off]]


async def _family(db: AsyncSession) -> tuple[Agent, Agent, Agent]:
    await ensure_unit_scope(
        db,
        CurrentUser(
            id=TEST_USER_ID, email="parts-owner@example.com", name="Owner", is_super_user=False
        ),
    )
    model = Model(id=uuid.uuid4(), provider="openai", model_name="m", display_name="M")
    parent, kept, off = (
        _agent(agent_id=uuid.uuid4(), user_id=TEST_USER_ID, name=name, model=model)
        for name in ("Parent", "Kept", "Off")
    )
    parent.sub_agent_links = [
        AgentSubAgentLink(
            parent_agent_id=parent.id, sub_agent_id=child.id, sub_agent=child, position=i
        )
        for i, child in enumerate((kept, off))
    ]
    db.add_all([model, parent, kept, off])
    await db.commit()
    return parent, kept, off


@pytest.mark.asyncio
async def test_a_switched_off_child_agent_and_a_childs_switched_off_skill_stay_out_of_the_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime.subagents import build_subagents_config

    parent, kept, off = await _family(db)
    on_skill = {"id": "s-on", "slug": "on"}
    off_skill = {"id": "s-off", "slug": "off"}
    prepared: list[AgentConfig] = []

    async def prepare(cfg: AgentConfig, **_: Any) -> SimpleNamespace:
        prepared.append(cfg)
        return SimpleNamespace(
            model=MagicMock(),
            tools=[],
            middleware=[],
            system_prompt=cfg.system_prompt,
            skills_sources=[],
            permissions=[],
            interrupt_on={},
        )

    async def plan(planner: RuntimeAccessPlanner, agent: Agent, **_: Any) -> RuntimeAccessPlan:
        subject = subject_for_agent(agent, planner.context, planner.identity)
        return RuntimeAccessPlan(agent, subject, (), (), (), ())

    monkeypatch.setattr(
        "app.agent_runtime.subagents.resolve_llm_api_key_for_agent", AsyncMock(return_value="k")
    )
    monkeypatch.setattr(
        "app.agent_runtime.subagents.chat_service.build_tools_config", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.agent_runtime.subagents.chat_service.build_agent_skills",
        lambda *_a, **_k: [on_skill, off_skill],
    )
    monkeypatch.setattr(RuntimeAccessPlanner, "plan", plan)
    monkeypatch.setattr("app.agent_runtime.subagents._prepare_runtime_components", prepare)
    identity = resolve_agent_run_identity(
        agent_id=parent.id,
        agent_owner_user_id=parent.user_id,
        runtime_name=parent.runtime_name,
        identity_mode=parent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=TEST_USER_ID,
    )

    def config(company: ToolRiskPolicy | None) -> AgentConfig:
        return AgentConfig(
            org_context=unit_context(TEST_USER_ID),
            provider="openai",
            model_name="m",
            api_key="k",
            base_url=None,
            system_prompt="x",
            tools_config=[],
            thread_id="t1",
            agent_id=str(parent.id),
            agent_name=parent.name,
            user_id=str(TEST_USER_ID),
            agent_owner_user_id=str(TEST_USER_ID),
            caller_user_id=str(TEST_USER_ID),
            credential_subject_user_id=str(identity.credential_subject_user_id),
            identity_mode=identity.identity_mode,
            agent_runtime_name=identity.runtime_name,
            tool_risk=company,
        )

    company = _company(**{f"agent:{off.id}": {"enabled": False}, "skill:s-off": {"enabled": False}})
    specs, names = await build_subagents_config(
        parent, db=db, parent_cfg=config(company), is_trigger_mode=False
    )

    assert [spec["name"] for spec in specs] == [kept.runtime_name]
    assert names == {kept.runtime_name: "Kept"}
    assert [cfg.agent_id for cfg in prepared] == [str(kept.id)]
    assert prepared[0].agent_skills == [on_skill]
    # The key the factory reads to ask before delegation is the child's own row.
    assert agent_ref(specs[0]["_moldy_actor_id"], "kept").key == f"agent:{kept.id}"

    prepared.clear()
    specs, _ = await build_subagents_config(
        parent, db=db, parent_cfg=config(None), is_trigger_mode=False
    )
    assert [spec["name"] for spec in specs] == [kept.runtime_name, off.runtime_name]
    assert prepared[0].agent_skills == [on_skill, off_skill]


@tool
def lookup(query: str) -> str:
    """Look something up."""
    return "found"


def _delegate(child: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": "find it", "subagent_type": child},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    )


async def _delegating_run(child: str, settings: dict[str, Any]) -> Any:
    tools: list[BaseTool] = [attach_tool_risk(lookup, risk_metadata_dict(ToolRiskLevel.READ_ONLY))]
    model = ToolRecordingModel(
        responses=[_delegate(child), AIMessage(content="child done"), AIMessage(content="끝")],
        offered=[],
    )
    agent = build_agent(
        model,
        tools,
        "You are a test agent.",
        checkpointer=MemorySaver(),
        interrupt_on={},
        subagents=[
            {
                "name": name,
                "description": f"{name} role",
                "system_prompt": "child",
                "tools": tools,
                "_moldy_actor_id": actor,
            }
            for name, actor in (("researcher", "c1"), ("writer", "c2"))
        ],
        tool_approval=ToolApprovalPlan(
            "chat",
            settings=ToolApprovalPolicyV1.model_validate({"tools": settings}),
            company=_company(),
        ),
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agent.ainvoke({"messages": [{"type": "human", "content": "해줘"}]}, config)
    return await agent.aget_state(config)


@pytest.mark.asyncio
async def test_a_real_run_asks_before_delegating_to_the_child_that_asks() -> None:
    settings = {"agent:c1": {"mode": "always"}}

    asked = await _delegating_run("researcher", settings)
    [interrupt] = [i for task in asked.tasks for i in task.interrupts]
    assert [a["name"] for a in interrupt.value["action_requests"]] == ["task"]
    assert interrupt.value["action_requests"][0]["args"]["subagent_type"] == "researcher"

    free = await _delegating_run("writer", settings)
    assert not free.next and free.values["messages"][-1].content == "끝"
