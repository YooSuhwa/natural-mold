"""Malformed persisted capability links fail before runtime content or decryption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import subagents
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate
from app.services import agent_invocation_service, agent_service
from app.services.agent_invocation_service import AgentInvocationPrincipal
from app.services.chat.runtime_context import build_tools_config, get_agent_with_tools

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
Operation = Literal["subagent", "tool"]


@dataclass(frozen=True, slots=True)
class RuntimeChildCase:
    parent: Agent
    context: OrgContext


@pytest.fixture
async def runtime_child_case(db: AsyncSession, private_scopes: PrivateScopes) -> RuntimeChildCase:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    parent = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Parent", system_prompt="Fixture", model_id=model.id, identity_mode="fixed"
        ),
        private_scopes.first,
    )
    child = await agent_service.create_agent(
        db,
        AgentCreate(
            name="Foreign child",
            system_prompt="Private fixture",
            model_id=model.id,
            identity_mode="fixed",
        ),
        private_scopes.second,
    )
    foreign = private_scopes.second
    tool = Tool(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign tool",
        definition_key="builtin:current_datetime",
        parameters={"private_parameter": "Other organization"},
    )
    db.add(tool)
    await db.flush()
    db.add_all(
        [
            AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child.id),
            AgentToolLink(agent_id=parent.id, tool_id=tool.id),
        ]
    )
    await db.commit()
    db.expunge_all()
    loaded = await get_agent_with_tools(db, parent.id, private_scopes.first)
    assert loaded is not None
    return RuntimeChildCase(loaded, private_scopes.first)


@pytest.mark.parametrize("operation", ["subagent", "tool"])
async def test_foreign_link_is_rejected_before_runtime_materialization(
    db: AsyncSession, runtime_child_case: RuntimeChildCase, operation: Operation
) -> None:
    parent, context = runtime_child_case.parent, runtime_child_case.context
    cfg = AgentConfig(
        org_context=context,
        provider="openai",
        model_name="fixture",
        api_key=None,
        base_url=None,
        system_prompt="Fixture",
        tools_config=[],
        thread_id="fixture-thread",
        agent_id=str(parent.id),
        user_id=str(context.user_id),
    )
    with pytest.MonkeyPatch.context() as patches:
        decrypt = AsyncMock(return_value=None)
        patches.setattr(subagents, "resolve_llm_api_key_for_agent", decrypt)
        components = RuntimeComponents(
            model_candidates=[],
            model=FakeListChatModel(responses=["Fixture"]),
            tools=[],
            middleware=[],
            system_prompt="Fixture",
            skills_sources=None,
            backend=None,
            memory_sources=None,
            permissions=[],
            interrupt_on=None,
        )
        prepare = AsyncMock(return_value=components)
        patches.setattr(subagents, "_prepare_runtime_components", prepare)
        match operation:
            case "subagent":
                with pytest.raises(AppError):
                    await subagents.build_subagents_config(
                        parent, db=db, parent_cfg=cfg, is_trigger_mode=False
                    )
            case "tool":
                with pytest.raises(AppError):
                    await build_tools_config(parent, context=context, db=db)
            case unreachable:
                assert_never(unreachable)
        decrypt.assert_not_awaited()
        prepare.assert_not_awaited()


async def test_loaded_invocation_graph_denies_before_parent_llm_resolution(
    db: AsyncSession, runtime_child_case: RuntimeChildCase
) -> None:
    parent, context = runtime_child_case.parent, runtime_child_case.context
    user = CurrentUser(id=context.user_id, email="fixture@example.com", name="Fixture")
    with pytest.MonkeyPatch.context() as patches:
        resolve = AsyncMock(return_value="B06_DUMMY_ONLY")
        patches.setattr(agent_invocation_service, "resolve_llm_api_key_for_agent", resolve)
        with pytest.raises(AppError) as captured:
            await agent_invocation_service.build_agent_config_for_loaded_agent(
                db,
                parent,
                context=context,
                thread_id="fixture-thread",
                principal=AgentInvocationPrincipal.chat_user(user),
                source="chat",
                current_user=user,
            )
        assert captured.value.status == 404
        resolve.assert_not_awaited()
