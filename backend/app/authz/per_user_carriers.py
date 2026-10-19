"""``per_user`` tools and MCP servers lent by fixed-identity agents (D15, 10.4, 11.3; F-04).

A ``per_user`` resource lends nothing when it runs as the caller. A fixed-identity
agent (the default) runs as its owner, so its ``per_user`` links use the owner's own
connection: whoever runs the agent uses the owner's account. Those links stay carriers:
``(kind, resource_id, credential)`` where the credential is the owner's connection, else
the resource's ``credential_id``, else the resource id itself as a placeholder ("the
owner's future connection"); ``personal_of`` then judges a placeholder by the resource's
``credential_definition_key`` and the D17 list skips it (no credential row yet).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import AGENT_IDENTITY_FIXED
from app.models.agent import Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding
from app.models.tool import AgentToolLink, Tool

PER_USER = "per_user"
Carrier = tuple[str, uuid.UUID, uuid.UUID]
Lent = tuple[str, uuid.UUID, uuid.UUID | None, uuid.UUID]  # kind, rid, credential, owner


async def _lent(db: AsyncSession, agents: set[uuid.UUID]) -> list[Lent]:
    fixed = (Agent.identity_mode == AGENT_IDENTITY_FIXED) & Agent.id.in_(agents)
    tools = await db.execute(
        select(Tool.id, Tool.credential_id, Agent.user_id)
        .join(AgentToolLink, AgentToolLink.tool_id == Tool.id)
        .join(Agent, Agent.id == AgentToolLink.agent_id)
        .where(fixed, Tool.credential_mode == PER_USER)
        .distinct()
    )
    servers = await db.execute(
        select(McpServer.id, McpServer.credential_id, Agent.user_id)
        .join(McpTool, McpTool.server_id == McpServer.id)
        .join(AgentMcpToolLink, AgentMcpToolLink.mcp_tool_id == McpTool.id)
        .join(Agent, Agent.id == AgentMcpToolLink.agent_id)
        .where(fixed, McpServer.credential_mode == PER_USER)
        .distinct()
    )
    found: list[Lent] = [("tool", rid, cid, owner) for rid, cid, owner in tools]
    found += [("mcp_server", rid, cid, owner) for rid, cid, owner in servers]
    return found


async def fixed_per_user_carriers(db: AsyncSession, agents: Iterable[uuid.UUID]) -> list[Carrier]:
    lent = await _lent(db, set(agents))
    if not lent:
        return []
    rows = await db.execute(
        select(Binding.resource_type, Binding.resource_id, Binding.user_id, Binding.credential_id)
        .where(Binding.resource_id.in_({rid for _, rid, _, _ in lent}))
        .order_by(Binding.created_at, Binding.id)
    )
    connected = {(kind, rid, user): cid for kind, rid, user, cid in rows.tuples().all()}
    return [
        (kind, rid, connected.get((kind, rid, owner)) or cid or rid)
        for kind, rid, cid, owner in lent
    ]


__all__ = ["fixed_per_user_carriers"]
