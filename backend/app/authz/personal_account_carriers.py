"""Which credentials a resource lends to the people it is shared with (10.4; E-05).

A carrier is ``(resource_type, resource_id, credential_id)``: the resource that holds the
credential in ``shared`` mode (D15). A ``per_user`` tool or MCP server shared directly
lends nothing (every caller connects their own account; F-04); one linked by a
fixed-identity agent still lends its owner's connection (``per_user_carriers``). A
``none`` row with a stale ``credential_id`` still counts, failing closed. An agent lends
its own model credential (``llm_credential_id``), its tools', MCP servers' and skill
bindings' credentials, and
everything its subagents lend, transitively (a shared agent runs its subagents).

Agent graphs are walked one depth level per query (cycle-safe: visited ids are never
queued again), and each carrier kind is read in one ``IN`` query for the whole set.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.authz.per_user_carriers import fixed_per_user_carriers
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool

Carrier = tuple[str, uuid.UUID, uuid.UUID]  # resource_type, resource_id, credential_id
PER_USER = "per_user"


async def _closure(
    db: AsyncSession,
    roots: Iterable[uuid.UUID],
    source: InstrumentedAttribute[uuid.UUID],
    target: InstrumentedAttribute[uuid.UUID],
) -> set[uuid.UUID]:
    seen = set(roots)
    frontier = set(seen)
    while frontier:
        found = set(await db.scalars(select(target).where(source.in_(frontier))))
        frontier = found - seen
        seen |= frontier
    return seen


async def with_subagents(db: AsyncSession, roots: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """``roots`` and every agent they call, at any depth."""
    return await _closure(
        db, roots, AgentSubAgentLink.parent_agent_id, AgentSubAgentLink.sub_agent_id
    )


async def with_parents(db: AsyncSession, roots: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """``roots`` and every agent that calls one of them, at any depth."""
    return await _closure(
        db, roots, AgentSubAgentLink.sub_agent_id, AgentSubAgentLink.parent_agent_id
    )


def _binding_ids(config: dict | None) -> list[uuid.UUID]:
    raw = (config or {}).get("credential_bindings") or {}
    found: list[uuid.UUID] = []
    for value in raw.values() if isinstance(raw, dict) else ():
        try:
            found.append(uuid.UUID(str(value)))
        except ValueError:
            continue  # the runtime skips malformed bindings too
    return found


async def agent_carriers(db: AsyncSession, agent_id: uuid.UUID) -> list[Carrier]:
    agents = await with_subagents(db, [agent_id])
    models = await db.execute(
        select(Agent.id, Agent.llm_credential_id).where(
            Agent.id.in_(agents), Agent.llm_credential_id.is_not(None)
        )
    )
    tools = await db.execute(
        select(Tool.id, Tool.credential_id)
        .join(AgentToolLink, AgentToolLink.tool_id == Tool.id)
        .where(
            AgentToolLink.agent_id.in_(agents),
            Tool.credential_id.is_not(None),
            Tool.credential_mode != PER_USER,
        )
        .distinct()
    )
    servers = await db.execute(
        select(McpServer.id, McpServer.credential_id)
        .join(McpTool, McpTool.server_id == McpServer.id)
        .join(AgentMcpToolLink, AgentMcpToolLink.mcp_tool_id == McpTool.id)
        .where(
            AgentMcpToolLink.agent_id.in_(agents),
            McpServer.credential_id.is_not(None),
            McpServer.credential_mode != PER_USER,
        )
        .distinct()
    )
    skills = await db.execute(
        select(AgentSkillLink.skill_id, AgentSkillLink.config).where(
            AgentSkillLink.agent_id.in_(agents)
        )
    )
    found: list[Carrier] = [("agent", rid, cid) for rid, cid in models if cid is not None]
    found += [("tool", rid, cid) for rid, cid in tools if cid is not None]
    found += [("mcp_server", rid, cid) for rid, cid in servers if cid is not None]
    found += await fixed_per_user_carriers(db, agents)
    for skill_id, config in skills:
        found += [("skill", skill_id, cid) for cid in _binding_ids(config)]
    return list(dict.fromkeys(found))


async def carriers(db: AsyncSession, row: object) -> list[Carrier]:
    if isinstance(row, Tool | McpServer) and row.credential_mode == PER_USER:
        return []
    match row:
        case Agent():
            return await agent_carriers(db, row.id)
        case Tool(credential_id=uuid.UUID() as cid):
            return [("tool", row.id, cid)]
        case McpServer(credential_id=uuid.UUID() as cid):
            return [("mcp_server", row.id, cid)]
        case Credential():
            return [("credential", row.id, row.id)]
    return []
