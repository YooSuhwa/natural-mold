"""Catalog inventory selects only global system resources and this company's models;
company shared credentials are listed for their fanout (E-07); the impact preview counts
this organization's agents linking one item (S09)."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.organization import Organization, Tenant
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from app.schemas.org_catalog import CatalogKind


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    kind: CatalogKind
    id: UUID
    name: str
    source: Literal["platform", "company"]
    available: bool = True


async def company_catalog_settings(
    db: AsyncSession, tenant_id: UUID, *, lock: bool = True
) -> Tenant:
    """The company row (locked for writers; ``lock=False`` for read-only previews)."""
    query = select(Tenant).where(Tenant.id == tenant_id)
    row = await db.scalar(
        (query.with_for_update() if lock else query).execution_options(populate_existing=True)
    )
    if row is None:
        from app.authz.error_codes import authorization_error

        raise authorization_error("not_found")
    return row


async def catalog_entries(db: AsyncSession, tenant_id: UUID) -> tuple[CatalogEntry, ...]:
    models = await db.execute(
        select(Model.id, Model.display_name, Model.is_visible, Model.tenant_id).where(
            or_(Model.tenant_id.is_(None), Model.tenant_id == tenant_id)
        )
    )
    entries = [
        CatalogEntry("model", key, name, "company" if company else "platform", visible)
        for key, name, visible, company in models
    ]
    # A disabled system tool or MCP server is unavailable: never fanned out, never enabled.
    tools = await db.execute(
        select(Tool.id, Tool.name, Tool.enabled).where(
            Tool.is_system.is_(True), Tool.user_id.is_(None)
        )
    )
    entries.extend(CatalogEntry("tool", key, name, "platform", on) for key, name, on in tools)
    # System skills and MCP servers keep the seeding operator in ``user_id`` (NOT NULL);
    # a platform row is one without organization and company (as candidates/runtime say).
    skills = await db.execute(
        select(Skill.id, Skill.name).where(
            Skill.is_system.is_(True), Skill.org_id.is_(None), Skill.tenant_id.is_(None)
        )
    )
    entries.extend(CatalogEntry("skill", key, name, "platform") for key, name in skills)
    servers = await db.execute(
        select(McpServer.id, McpServer.name, McpServer.status).where(
            McpServer.is_system.is_(True),
            McpServer.org_id.is_(None),
            McpServer.tenant_id.is_(None),
        )
    )
    entries.extend(
        CatalogEntry("mcp_server", key, name, "platform", status != "disabled")
        for key, name, status in servers
    )
    return tuple(sorted(entries, key=lambda item: (item.kind, item.name.casefold(), item.id)))


async def company_credential_ids(db: AsyncSession, tenant_id: UUID) -> tuple[UUID, ...]:
    """Active company shared credentials (``scope='tenant'``, no organization)."""
    found = await db.scalars(
        select(Credential.id)
        .where(
            Credential.scope == "tenant",
            Credential.tenant_id == tenant_id,
            Credential.org_id.is_(None),
            Credential.status == "active",
        )
        .order_by(Credential.id)
    )
    return tuple(found)


async def catalog_impact(
    db: AsyncSession, organization: Organization, kind: CatalogKind, item_id: UUID
) -> tuple[int, int]:
    """(agents of the organization linking the item, agents that cannot run without it).

    Only a model is required today (the primary ``model_id``); per-link ``required`` flags
    for tools, skills and MCP servers arrive with F-01 (m86).
    """
    agents = select(Agent.id).where(
        Agent.org_id == organization.id,
        Agent.tenant_id == organization.tenant_id,
        Agent.archived_at.is_(None),
    )
    match kind:
        case "model":
            linked = agents.where(Agent.model_id == item_id)
        case "tool":
            linked = agents.join(AgentToolLink, AgentToolLink.agent_id == Agent.id).where(
                AgentToolLink.tool_id == item_id
            )
        case "skill":
            linked = agents.join(AgentSkillLink, AgentSkillLink.agent_id == Agent.id).where(
                AgentSkillLink.skill_id == item_id
            )
        case "mcp_server":
            linked = (
                agents.join(AgentMcpToolLink, AgentMcpToolLink.agent_id == Agent.id)
                .join(McpTool, McpTool.id == AgentMcpToolLink.mcp_tool_id)
                .where(McpTool.server_id == item_id)
            )
    affected = await db.scalar(select(func.count()).select_from(linked.distinct().subquery()))
    return int(affected or 0), int(affected or 0) if kind == "model" else 0
