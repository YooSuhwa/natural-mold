"""Normal Assistant writers preserve hidden physical resource associations."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools import write_tools
from app.agent_runtime.assistant.tools.helpers import get_agent_with_eager_load
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.organization import OrganizationMember
from tests.authz.assistant_projection_fixture import (
    assistant_sessions as assistant_sessions,
)
from tests.authz.assistant_projection_fixture import (
    binding_target as binding_target,
)
from tests.authz.assistant_projection_fixture import create_binding, visible_ids

if TYPE_CHECKING:
    from tests.authz.assistant_projection_fixture import BindingTarget
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_agent_scope"]


async def test_normal_assistant_writer_controls_preserve_hidden_physical_links(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    hidden = await create_binding(db, binding_target, own)
    await hidden.move_to(db, private_scopes.second)
    visible = await create_binding(db, replace(binding_target, marker="visible-marker"), own)
    await visible.rename_visible(db, "current-visible-marker")
    candidate = await create_binding(db, replace(binding_target, marker="candidate-marker"), own)
    # Detach the candidate via the same normal callback used later for remove controls.
    tools = {
        tool.name: tool for tool in write_tools.build_write_tools(db, binding_target.agent_id, own)
    }
    match binding_target.kind:
        case "tool":
            add_name, remove_name = "add_tool_to_agent", "remove_tool_from_agent"
            visible_args = {"tool_names": ["current-visible-marker"]}
            candidate_args = {"tool_names": [candidate.target.marker]}
            hidden_args = {"tool_names": [hidden.target.marker]}
        case "skill":
            add_name, remove_name = "add_skill_to_agent", "remove_skill_from_agent"
            visible_args = {"skill_names": ["current-visible-marker"]}
            candidate_args = {"skill_names": [candidate.target.marker]}
            hidden_args = {"skill_names": [hidden.target.marker]}
        case "mcp":
            add_name, remove_name = "add_mcp_tool_to_agent", "remove_mcp_tool_from_agent"
            visible_args = {"mcp_tool_names": ["current-visible-marker"]}
            candidate_args = {"mcp_tool_names": [candidate.target.marker]}
            hidden_args = {"mcp_tool_names": [hidden.target.marker]}
        case "subagent":
            add_name, remove_name = "add_subagent_to_agent", "remove_subagent_from_agent"
            visible_args = {"agent_ids": [str(visible.linked_id)]}
            candidate_args = {"agent_ids": [str(candidate.linked_id)]}
            hidden_args = {"agent_ids": [str(hidden.linked_id)]}
        case unreachable:
            assert_never(unreachable)
    expected = {hidden.linked_id, visible.linked_id}
    assert "완료" in await tools[remove_name].ainvoke(candidate_args)
    assert await hidden.persisted_ids(db) == expected
    assert "완료" in await tools["update_agent_metadata"].ainvoke({"name": "updated-parent"})
    assert await hidden.persisted_ids(db) == expected
    # Hidden targets are omitted from write metadata/removal without deleting their links.
    await tools[remove_name].ainvoke(hidden_args)
    assert await hidden.persisted_ids(db) == expected
    assert "추가" in await tools[add_name].ainvoke(candidate_args)
    expected.add(candidate.linked_id)
    assert await hidden.persisted_ids(db) == expected
    assert "완료" in await tools[remove_name].ainvoke(visible_args)
    expected.remove(visible.linked_id)
    assert await hidden.persisted_ids(db) == expected
    assert "완료" in await tools[remove_name].ainvoke(candidate_args)
    assert await hidden.persisted_ids(db) == {hidden.linked_id}
    projected = await get_agent_with_eager_load(db, binding_target.agent_id, own)
    assert projected is not None and projected.name == "updated-parent"
    assert visible_ids(projected, binding_target.kind) == set()


async def test_assistant_add_subagent_rejects_foreign_candidate(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    candidate = Agent(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        model_id=binding_target.model_id,
        name="Foreign assistant candidate",
        system_prompt="Synthetic",
    )
    db.add(candidate)
    await db.commit()
    tools = {
        tool.name: tool for tool in write_tools.build_write_tools(db, binding_target.agent_id, own)
    }
    result = await tools["add_subagent_to_agent"].ainvoke({"agent_ids": [str(candidate.id)]})
    assert "찾을 수 없음" in result
    assert (
        await db.scalar(
            select(AgentSubAgentLink.parent_agent_id).where(
                AgentSubAgentLink.parent_agent_id == binding_target.agent_id,
                AgentSubAgentLink.sub_agent_id == candidate.id,
            )
        )
        is None
    )


async def test_assistant_root_mutation_rejects_revoked_actor(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    linked = await create_binding(
        db,
        replace(binding_target, kind="subagent", marker="revoked-remove-control"),
        own,
    )
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == own.org_id,
            OrganizationMember.user_id == own.user_id,
        )
        .values(status="removed")
    )
    await db.commit()
    tools = {
        tool.name: tool for tool in write_tools.build_write_tools(db, binding_target.agent_id, own)
    }
    with pytest.raises(AppError) as denied:
        await tools["remove_subagent_from_agent"].ainvoke({"agent_ids": [str(linked.linked_id)]})
    assert denied.value.status == 403
    assert await linked.persisted_ids(db) == {linked.linked_id}


async def test_assistant_root_mutation_rejects_foreign_root(
    db: AsyncSession, binding_target: BindingTarget, private_scopes: PrivateScopes
) -> None:
    linked = await create_binding(
        db,
        replace(binding_target, kind="subagent", marker="foreign-remove-control"),
        private_scopes.first,
    )
    tools = {
        tool.name: tool
        for tool in write_tools.build_write_tools(
            db,
            binding_target.agent_id,
            private_scopes.second,
        )
    }
    with pytest.raises(AppError) as denied:
        await tools["remove_subagent_from_agent"].ainvoke({"agent_ids": [str(linked.linked_id)]})
    assert denied.value.status == 404
    assert await linked.persisted_ids(db) == {linked.linked_id}
