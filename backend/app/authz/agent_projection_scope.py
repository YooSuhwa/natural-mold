"""Filter Agent presentation links by their persisted resource parents."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool


@dataclass(frozen=True, slots=True)
class AgentPresentation:
    agent: Agent
    unread_count: int


def tool_link_scope(context: OrgContext) -> ColumnElement[bool]:
    return AgentToolLink.tool_id.in_(resource_candidates(select(Tool.id), Tool, context))


def mcp_link_scope(context: OrgContext) -> ColumnElement[bool]:
    return AgentMcpToolLink.mcp_tool_id.in_(
        select(McpTool.id)
        .join(McpServer, McpTool.server_id == McpServer.id)
        .where(private_scope_predicate(McpServer, context))
    )


def projection_options(context: OrgContext) -> tuple[LoaderOption, ...]:
    skill_scope = AgentSkillLink.skill_id.in_(
        select(Skill.id).where(private_scope_predicate(Skill, context))
    )
    subagent_scope = AgentSubAgentLink.sub_agent_id.in_(
        select(Agent.id).where(private_scope_predicate(Agent, context))
    )
    direct = (
        selectinload(Agent.model),
        selectinload(Agent.tool_links.and_(tool_link_scope(context))).selectinload(
            AgentToolLink.tool
        ),
        selectinload(Agent.mcp_tool_links.and_(mcp_link_scope(context))).selectinload(
            AgentMcpToolLink.mcp_tool
        ),
        selectinload(Agent.skill_links.and_(skill_scope)).selectinload(AgentSkillLink.skill),
    )
    # A child can also be a root in the same list. Refresh its direct
    # relationships with the same scope so joined population cannot leave
    # that root with expired presentation attributes in the async serializer.
    return (
        *direct,
        selectinload(Agent.sub_agent_links.and_(subagent_scope))
        .joinedload(AgentSubAgentLink.sub_agent)
        .options(*direct),
    )


async def load_agent_presentation(
    db: AsyncSession, agent_id: UUID, context: OrgContext
) -> AgentPresentation | None:
    """Read current presentation links without changing persisted associations."""
    with db.no_autoflush:
        await require_private_actor(db, context)
        agent = await db.scalar(
            select(Agent)
            .where(Agent.id == agent_id, private_scope_predicate(Agent, context))
            .options(*projection_options(context))
            .execution_options(autoflush=False, populate_existing=True)
        )
        if agent is not None:
            unread = await db.scalar(
                select(func.coalesce(func.sum(Conversation.unread_count), 0))
                .where(
                    Conversation.agent_id == agent_id,
                    private_scope_predicate(Conversation, context),
                )
                .execution_options(autoflush=False)
            )
            return AgentPresentation(agent, int(unread or 0))
    return None
