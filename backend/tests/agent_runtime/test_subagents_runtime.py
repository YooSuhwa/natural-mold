from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import AgentConfig
from app.agent_runtime.identity import (
    AGENT_IDENTITY_FIXED,
    AGENT_IDENTITY_PER_USER,
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.model import Model


def _agent(
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    model: Model,
    identity_mode: str = AGENT_IDENTITY_FIXED,
) -> Agent:
    agent = Agent(
        id=agent_id,
        user_id=user_id,
        name=name,
        description=f"{name} role",
        system_prompt=f"{name} prompt",
        model_id=model.id,
        model=model,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=identity_mode,
    )
    agent.tool_links = []
    agent.mcp_tool_links = []
    agent.skill_links = []
    agent.sub_agent_links = []
    return agent


@pytest.mark.asyncio
async def test_build_subagents_config_uses_child_identity_tools_skills_and_interrupts(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime.subagents import build_subagents_config

    owner_id = uuid.uuid4()
    caller_id = uuid.uuid4()
    parent_model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    child_model = Model(
        id=uuid.uuid4(),
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        display_name="Claude",
    )
    parent = _agent(
        agent_id=uuid.uuid4(),
        user_id=owner_id,
        name="Parent",
        model=parent_model,
    )
    child = _agent(
        agent_id=uuid.uuid4(),
        user_id=owner_id,
        name="Researcher",
        model=child_model,
        identity_mode=AGENT_IDENTITY_PER_USER,
    )
    parent.sub_agent_links = [
        AgentSubAgentLink(
            parent_agent_id=parent.id,
            sub_agent_id=child.id,
            sub_agent=child,
            position=0,
        )
    ]
    parent_identity = resolve_agent_run_identity(
        agent_id=parent.id,
        agent_owner_user_id=parent.user_id,
        runtime_name=parent.runtime_name,
        identity_mode=parent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=caller_id,
    )
    parent_cfg = AgentConfig(
        provider=parent_model.provider,
        model_name=parent_model.model_name,
        api_key="parent-key",
        base_url=None,
        system_prompt=parent.system_prompt,
        tools_config=[{"name": "parent_only_tool"}],
        thread_id="thread-1",
        agent_id=str(parent.id),
        agent_name=parent.name,
        user_id=str(parent.user_id),
        agent_owner_user_id=str(parent.user_id),
        caller_user_id=str(caller_id),
        credential_subject_user_id=str(parent_identity.credential_subject_user_id),
        identity_mode=parent_identity.identity_mode,
        agent_runtime_name=parent_identity.runtime_name,
    )

    observed_cfgs: list[AgentConfig] = []

    async def fake_prepare_runtime_components(
        cfg: AgentConfig,
        *,
        is_trigger_mode: bool,
        include_ask_user: bool,
        include_agent_memory_file: bool,
    ) -> SimpleNamespace:
        observed_cfgs.append(cfg)
        assert is_trigger_mode is False
        assert include_ask_user is False
        assert include_agent_memory_file is False
        child_tool = MagicMock()
        child_tool.name = "child_only_tool"
        return SimpleNamespace(
            model=MagicMock(),
            tools=[child_tool],
            middleware=[],
            system_prompt=cfg.system_prompt,
            skills_sources=["/runtime/thread-1/agents/agent_child/skills/"],
            permissions=[],
            interrupt_on={"child_only_tool": True},
        )

    async def fake_build_tools_config(
        agent: Agent,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        assert agent is child
        identity = kwargs["identity"]
        assert identity.credential_subject_user_id == caller_id
        return [{"name": "child_only_tool"}]

    monkeypatch.setattr(
        "app.agent_runtime.subagents.resolve_llm_api_key_for_agent",
        AsyncMock(return_value="child-key"),
    )
    monkeypatch.setattr(
        "app.agent_runtime.subagents.chat_service.build_tools_config",
        fake_build_tools_config,
    )
    monkeypatch.setattr(
        "app.agent_runtime.subagents.chat_service.build_agent_skills",
        lambda agent: [{"slug": "child-skill"}] if agent is child else [],
    )
    monkeypatch.setattr(
        "app.agent_runtime.subagents._prepare_runtime_components",
        fake_prepare_runtime_components,
    )

    subagents, display_names = await build_subagents_config(
        parent,
        db=db,
        parent_cfg=parent_cfg,
        is_trigger_mode=False,
    )

    assert display_names == {child.runtime_name: "Researcher"}
    assert len(subagents) == 1
    spec = subagents[0]
    assert spec["name"] == child.runtime_name
    assert "Researcher" in spec["description"]
    assert spec["tools"][0].name == "child_only_tool"
    assert spec["skills"] == ["/runtime/thread-1/agents/agent_child/skills/"]
    assert spec["interrupt_on"] == {"child_only_tool": True}
    assert observed_cfgs[0].provider == "anthropic"
    assert observed_cfgs[0].api_key == "child-key"
    assert observed_cfgs[0].credential_subject_user_id == str(caller_id)
