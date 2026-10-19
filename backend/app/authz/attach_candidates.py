"""Non-owned resources an actor may attach to an agent (plan 9.1 PUT/POST rows, 10.6; F-02).

The agent binding validators accept owned (and platform) rows as before. Once a type is
enforced, ids that fail that owner check are re-tried here: a candidate row of the
current organization shared to the actor, one of their groups or the organization
(``candidates.candidate_predicate``) that OpenFGA confirms with the 10.6 permission —
``can_use`` for tools and skills, ``can_invoke`` for MCP tools, ``can_run`` for subagents.
Account-carrying rows still meet D17 (``attach_rules.require_attachable``) afterwards.
Legacy (or a type not enforced): nothing is added, so validation is unchanged.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.candidates import candidate_predicate
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.runtime import runtime_authz
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool

ATTACH_PERMISSIONS = {
    "tool": "can_use",
    "skill": "can_use",
    "mcp_tool": "can_invoke",
    "agent": "can_run",
}


def _candidates(kind: str, ids: list[UUID], ctx: OrgContext) -> Select[tuple[UUID]]:
    match kind:
        case "tool":
            return select(Tool.id).where(Tool.id.in_(ids), candidate_predicate(Tool, kind, ctx))
        case "skill":
            return select(Skill.id).where(Skill.id.in_(ids), candidate_predicate(Skill, kind, ctx))
        case "mcp_tool":
            return (
                select(McpTool.id)
                .join(McpServer, McpServer.id == McpTool.server_id)
                .where(McpTool.id.in_(ids), candidate_predicate(McpServer, "mcp_server", ctx))
            )
        case _:
            return select(Agent.id).where(
                Agent.id.in_(ids),
                candidate_predicate(Agent, "agent", ctx),
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                Agent.archived_at.is_(None),
            )


async def shared_attachable(
    db: AsyncSession, ctx: OrgContext, kind: str, ids: Iterable[UUID]
) -> set[UUID]:
    """The subset of ``ids`` (not owned) the actor may attach as shared ``kind`` rows."""
    wanted = sorted(set(ids), key=lambda item: item.bytes)
    authz = runtime_authz()
    if not wanted or not authz.enforces(kind):
        return set()
    with db.no_autoflush:
        found = list(await db.scalars(_candidates(kind, wanted, ctx)))
    if not found:
        return set()
    permission = ATTACH_PERMISSIONS[kind]
    objects = [f"{kind}:{rid}" for rid in found]
    allowed = await authz.filter_visible(Principal.for_user(ctx), permission, objects, legacy=[])
    return {UUID(obj.partition(":")[2]) for obj in allowed}
