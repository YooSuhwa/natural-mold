"""The delegated links the delegation validator re-checks (plan 10.6, D14, D17; F-03).

One row per active link of a live agent in one organization: the delegated object (MCP
tools by server, as F-02 grants them), the delegator (``delegated_by``, or
``agents.llm_credential_delegated_by`` for the model credential), whether the object
may carry a delegation at all (same organization/company, not archived — D21, F-02
``_grantable``) and the OpenFGA checks the delegator must still pass:

* tools and skills ``can_use``, MCP tools ``can_invoke`` (the tool row decides, so a
  ``blocked`` tool grant breaks only that link), subagents ``can_run``, credentials
  ``can_use``;
* account carriers (a credential, a tool / MCP server holding one, not platform rows)
  also ``can_share`` unless the delegator owns them (D17, ``attach_rules``).

Platform credentials are not catalog items and are skipped. Broken links are not
returned: they stay broken until an editor re-attaches them.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.authz.agent_delegations import LINK_SOURCES, Key
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool

Check = tuple[str, str]  # (permission, FGA object)


@dataclass(frozen=True, slots=True)
class DelegationScope:
    """Which links to re-check; ``None`` fields do not narrow (the whole organization)."""

    org_id: UUID
    objects: frozenset[Key] | None = None
    delegators: frozenset[UUID] | None = None


@dataclass(frozen=True, slots=True)
class DelegatedLink:
    table: str  # a ``LINK_SOURCES`` value
    agent_id: UUID
    link_id: UUID  # second key column (tool, MCP tool, skill, subagent, credential id)
    obj: Key  # the delegated object (MCP tools: their server)
    delegator: UUID | None
    grantable: bool
    checks: tuple[Check, ...]


def _narrow(
    query: Select[Any], scope: DelegationScope, kind: str, obj: Any, by: Any
) -> Select[Any]:
    if scope.objects is None and scope.delegators is None:
        return query
    ids = [rid for k, rid in scope.objects or () if k == kind]
    return query.where(or_(obj.in_(ids), by.in_(sorted(scope.delegators or ()))))


def _grantable(org: UUID, tenant: UUID, row_org: UUID | None, row_tenant: UUID | None) -> bool:
    return row_org in (None, org) and row_tenant in (None, tenant)


def _checks(
    base: Check, kind: str, rid: UUID, *, carrier: bool, owner: UUID | None, by: UUID | None
) -> tuple[Check, ...]:
    share = carrier and owner != by
    return (base, ("can_share", f"{kind}:{rid}")) if share else (base,)


def _live(query: Select[Any], org_id: UUID) -> Select[Any]:
    return query.where(Agent.org_id == org_id, Agent.archived_at.is_(None))


async def delegated_links(
    db: AsyncSession, scope: DelegationScope, tenant_id: UUID
) -> list[DelegatedLink]:
    """Active links (and model credentials) of the organization's live agents in ``scope``."""
    org = scope.org_id
    found: list[DelegatedLink] = []
    tools = _live(
        select(AgentToolLink, Tool)
        .join(Agent, Agent.id == AgentToolLink.agent_id)
        .join(Tool, Tool.id == AgentToolLink.tool_id)
        .where(AgentToolLink.link_status == "active"),
        org,
    )
    for link, tool in (
        await db.execute(_narrow(tools, scope, "tool", Tool.id, AgentToolLink.delegated_by))
    ).all():
        carrier = tool.credential_id is not None and not tool.is_system
        found.append(
            DelegatedLink(
                LINK_SOURCES["tool"],
                link.agent_id,
                tool.id,
                ("tool", tool.id),
                link.delegated_by,
                _grantable(org, tenant_id, tool.org_id, tool.tenant_id),
                _checks(
                    ("can_use", f"tool:{tool.id}"),
                    "tool",
                    tool.id,
                    carrier=carrier,
                    owner=tool.user_id,
                    by=link.delegated_by,
                ),
            )
        )
    found += await _mcp_links(db, scope, tenant_id)
    skills = _live(
        select(AgentSkillLink, Skill)
        .join(Agent, Agent.id == AgentSkillLink.agent_id)
        .join(Skill, Skill.id == AgentSkillLink.skill_id)
        .where(AgentSkillLink.link_status == "active"),
        org,
    )
    narrowed = _narrow(skills, scope, "skill", Skill.id, AgentSkillLink.delegated_by)
    for link, skill in (await db.execute(narrowed)).all():
        found.append(
            DelegatedLink(
                LINK_SOURCES["skill"],
                link.agent_id,
                skill.id,
                ("skill", skill.id),
                link.delegated_by,
                _grantable(org, tenant_id, skill.org_id, skill.tenant_id),
                (("can_use", f"skill:{skill.id}"),),
            )
        )
    found += await _subagent_links(db, scope, tenant_id)
    found += await _credential_links(db, scope, tenant_id)
    return found


async def _mcp_links(
    db: AsyncSession, scope: DelegationScope, tenant_id: UUID
) -> list[DelegatedLink]:
    org = scope.org_id
    query = _live(
        select(AgentMcpToolLink, McpServer)
        .join(Agent, Agent.id == AgentMcpToolLink.agent_id)
        .join(McpTool, McpTool.id == AgentMcpToolLink.mcp_tool_id)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(AgentMcpToolLink.link_status == "active"),
        org,
    )
    query = _narrow(query, scope, "mcp_server", McpServer.id, AgentMcpToolLink.delegated_by)
    found: list[DelegatedLink] = []
    for link, server in (await db.execute(query)).all():
        carrier = server.credential_id is not None and not server.is_system
        base = ("can_invoke", f"mcp_tool:{link.mcp_tool_id}")
        found.append(
            DelegatedLink(
                LINK_SOURCES["mcp_server"],
                link.agent_id,
                link.mcp_tool_id,
                ("mcp_server", server.id),
                link.delegated_by,
                _grantable(org, tenant_id, server.org_id, server.tenant_id),
                _checks(
                    base,
                    "mcp_server",
                    server.id,
                    carrier=carrier,
                    owner=server.user_id,
                    by=link.delegated_by,
                ),
            )
        )
    return found


async def _subagent_links(
    db: AsyncSession, scope: DelegationScope, tenant_id: UUID
) -> list[DelegatedLink]:
    child = aliased(Agent, name="child")
    query = _live(
        select(
            AgentSubAgentLink,
            child.org_id,
            child.tenant_id,
            child.archived_at,
        )
        .join(Agent, Agent.id == AgentSubAgentLink.parent_agent_id)
        .join(child, child.id == AgentSubAgentLink.sub_agent_id)
        .where(AgentSubAgentLink.link_status == "active"),
        scope.org_id,
    )
    query = _narrow(query, scope, "agent", child.id, AgentSubAgentLink.delegated_by)
    found: list[DelegatedLink] = []
    for link, row_org, row_tenant, archived in (await db.execute(query)).all():
        rid = link.sub_agent_id
        found.append(
            DelegatedLink(
                LINK_SOURCES["agent"],
                link.parent_agent_id,
                rid,
                ("agent", rid),
                link.delegated_by,
                archived is None and _grantable(scope.org_id, tenant_id, row_org, row_tenant),
                (("can_run", f"agent:{rid}"),),
            )
        )
    return found


async def _credential_links(
    db: AsyncSession, scope: DelegationScope, tenant_id: UUID
) -> list[DelegatedLink]:
    query = _live(
        select(Agent.id, Agent.llm_credential_delegated_by, Credential)
        .join(Credential, Credential.id == Agent.llm_credential_id)
        .where(Credential.is_system.is_(False)),
        scope.org_id,
    )
    query = _narrow(query, scope, "credential", Credential.id, Agent.llm_credential_delegated_by)
    found: list[DelegatedLink] = []
    for agent_id, by, credential in (await db.execute(query)).all():
        rid = credential.id
        found.append(
            DelegatedLink(
                LINK_SOURCES["credential"],
                agent_id,
                rid,
                ("credential", rid),
                by,
                _grantable(scope.org_id, tenant_id, credential.org_id, credential.tenant_id),
                _checks(
                    ("can_use", f"credential:{rid}"),
                    "credential",
                    rid,
                    carrier=True,
                    owner=credential.user_id,
                    by=by,
                ),
            )
        )
    return found
