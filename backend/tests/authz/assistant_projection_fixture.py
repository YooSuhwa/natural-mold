"""Synthetic Agent associations for Assistant presentation regression controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, assert_never
from uuid import UUID

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools import read_tools, write_tools
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.test_private_agent_scope import AgentCase

type Kind = Literal["tool", "skill", "mcp", "subagent"]
_KIND_ADAPTER: Final = TypeAdapter(Kind)


@dataclass(frozen=True, slots=True)
class BindingTarget:
    agent_id: UUID
    model_id: UUID
    kind: Kind
    marker: str


@dataclass(frozen=True, slots=True)
class ChildBinding:
    target: BindingTarget
    resource: Tool | Skill | McpServer | Agent
    linked_id: UUID

    async def move_to(self, db: AsyncSession, context: OrgContext) -> None:
        """Move only the synthetic parent, keeping loaded identity-map state stale."""
        await db.execute(
            update(type(self.resource))
            .where(type(self.resource).id == self.resource.id)
            .values(org_id=context.org_id, tenant_id=context.tenant_id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    async def rename_visible(self, db: AsyncSession, name: str) -> None:
        """Update the metadata actually exposed by the Assistant's linked child."""
        match self.target.kind:
            case "tool" | "skill" | "subagent":
                model = type(self.resource)
                target_id = self.resource.id
            case "mcp":
                model = McpTool
                target_id = self.linked_id
            case unreachable:
                assert_never(unreachable)
        await db.execute(
            update(model)
            .where(model.id == target_id)
            .values(name=name)
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    async def persisted_ids(self, db: AsyncSession) -> set[UUID]:
        """Read physical associations independently of projected ORM collections."""
        match self.target.kind:
            case "tool":
                query = select(AgentToolLink.tool_id).where(
                    AgentToolLink.agent_id == self.target.agent_id
                )
            case "skill":
                query = select(AgentSkillLink.skill_id).where(
                    AgentSkillLink.agent_id == self.target.agent_id
                )
            case "mcp":
                query = select(AgentMcpToolLink.mcp_tool_id).where(
                    AgentMcpToolLink.agent_id == self.target.agent_id
                )
            case "subagent":
                query = select(AgentSubAgentLink.sub_agent_id).where(
                    AgentSubAgentLink.parent_agent_id == self.target.agent_id
                )
            case unreachable:
                assert_never(unreachable)
        return set(await db.scalars(query))


async def create_binding(
    db: AsyncSession, target: BindingTarget, context: OrgContext
) -> ChildBinding:
    """Create an ordinary resource in the chosen synthetic scope and link it."""
    match target.kind:
        case "tool":
            resource = Tool(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name=target.marker,
                description=target.marker,
                definition_key="http_request",
            )
        case "skill":
            resource = Skill(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name=target.marker,
                description=target.marker,
                slug=target.marker,
                kind="text",
            )
        case "mcp":
            resource = McpServer(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name=target.marker,
                transport="streamable_http",
                url="https://invalid.local",
            )
        case "subagent":
            resource = Agent(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name=target.marker,
                description=target.marker,
                system_prompt="Synthetic",
                model_id=target.model_id,
            )
        case unreachable:
            assert_never(unreachable)
    db.add(resource)
    await db.flush()
    linked_id = resource.id
    match target.kind:
        case "tool":
            link = AgentToolLink(agent_id=target.agent_id, tool_id=linked_id)
        case "skill":
            link = AgentSkillLink(agent_id=target.agent_id, skill_id=linked_id)
        case "mcp":
            mcp_tool = McpTool(server_id=resource.id, name=target.marker, enabled=True)
            db.add(mcp_tool)
            await db.flush()
            linked_id = mcp_tool.id
            link = AgentMcpToolLink(agent_id=target.agent_id, mcp_tool_id=linked_id)
        case "subagent":
            link = AgentSubAgentLink(
                parent_agent_id=target.agent_id, sub_agent_id=linked_id, position=0
            )
        case unreachable:
            assert_never(unreachable)
    db.add(link)
    await db.commit()
    return ChildBinding(target, resource, linked_id)


def visible_ids(agent: Agent, kind: Kind) -> set[UUID]:
    match kind:
        case "tool":
            return {link.tool_id for link in agent.tool_links}
        case "skill":
            return {link.skill_id for link in agent.skill_links}
        case "mcp":
            return {link.mcp_tool_id for link in agent.mcp_tool_links}
        case "subagent":
            return {link.sub_agent_id for link in agent.sub_agent_links}
        case unreachable:
            assert_never(unreachable)


def visible_names(agent: Agent, kind: Kind) -> set[str]:
    match kind:
        case "tool":
            return {link.tool.name for link in agent.tool_links}
        case "skill":
            return {link.skill.name for link in agent.skill_links}
        case "mcp":
            return {link.mcp_tool.name for link in agent.mcp_tool_links}
        case "subagent":
            return {link.sub_agent.name for link in agent.sub_agent_links}
        case unreachable:
            assert_never(unreachable)


@pytest.fixture(autouse=True)
def assistant_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_tools, "async_session_factory", TestSession)
    monkeypatch.setattr(write_tools, "async_session_factory", TestSession)


@pytest.fixture(params=["tool", "skill", "mcp", "subagent"])
def binding_target(request: pytest.FixtureRequest, agent_case: AgentCase) -> BindingTarget:
    kind = _KIND_ADAPTER.validate_python(request.param)
    return BindingTarget(agent_case.agent_id, agent_case.model_id, kind, "assistant-marker")
