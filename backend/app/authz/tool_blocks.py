"""Keeping one MCP tool from named people or teams (tool-governance-plan 3.6).

A block is a ``resource_grants`` row with the relation ``blocked`` on the ``mcp_tool``:
the model's ``can_invoke: can_use from server but not blocked`` reads it in enforce mode
(attach candidates, run planning, delegation links), written through ``GrantService`` so
the row and its tuples stay together. It is also read straight from the database when a
run's tools are assembled and before every call, so the block holds whatever the
authorization mode is and stops a run already in progress (3.11).

People and teams only: blocking the whole organization is switching the tool off, and the
server's owner cannot be blocked (they could lift it themselves).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.error_codes import authorization_error
from app.authz.grant_validation import invalid
from app.authz.grants import GrantChange, GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.principal_names import user_display_names
from app.authz.registration_state import registered
from app.authz.sharing import lock_org_row
from app.database import SessionTenant, tenant_session
from app.models.authz import ResourceGrant
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization_group import Group, GroupMember

RESOURCE_TYPE: Final = "mcp_tool"
RELATION: Final = "blocked"
PRINCIPALS: Final = ("user", "group")

# Why a call is refused, as recorded in the ``mcp.tool_call`` decision.
TOOL_OFF: Final = "tool_disabled"
NOT_REGISTERED: Final = "registration_not_approved"
BLOCKED: Final = "tool_blocked"


@dataclass(frozen=True, slots=True)
class Block:
    grant_id: UUID
    principal_type: str
    principal_id: UUID
    display_name: str | None
    member_count: int | None
    granted_by: UUID | None
    granted_at: datetime


def _blocks(tool_ids: Collection[UUID]) -> tuple[ColumnElement[bool], ...]:
    return (
        ResourceGrant.resource_type == RESOURCE_TYPE,
        ResourceGrant.relation == RELATION,
        ResourceGrant.state == "active",
        ResourceGrant.resource_id.in_(tool_ids),
    )


async def list_blocks(db: AsyncSession, ctx: OrgContext, tool: McpTool) -> list[Block]:
    rows = (
        await db.scalars(
            select(ResourceGrant)
            .where(*_blocks([tool.id]), ResourceGrant.org_id == ctx.org_id)
            .order_by(ResourceGrant.granted_at, ResourceGrant.id)
        )
    ).all()
    users = {row.principal_id for row in rows if row.principal_type == "user"}
    groups = {row.principal_id for row in rows if row.principal_type == "group"}
    user_names = await user_display_names(db, users)
    group_names: dict[UUID, str] = {}
    sizes: dict[UUID, int] = {}
    if groups:
        named = await db.execute(select(Group.id, Group.name).where(Group.id.in_(groups)))
        group_names = dict(named.tuples().all())
        counted = await db.execute(
            select(GroupMember.group_id, func.count())
            .where(GroupMember.group_id.in_(groups))
            .group_by(GroupMember.group_id)
        )
        sizes = dict(counted.tuples().all())
    return [
        Block(
            grant_id=row.id,
            principal_type=row.principal_type,
            principal_id=row.principal_id,
            display_name=(user_names if row.principal_type == "user" else group_names).get(
                row.principal_id
            ),
            member_count=sizes.get(row.principal_id, 0) if row.principal_type == "group" else None,
            granted_by=row.granted_by,
            granted_at=row.granted_at,
        )
        for row in rows
    ]


async def block(
    db: AsyncSession,
    ctx: OrgContext,
    server: McpServer,
    tool: McpTool,
    principal_type: str,
    principal_id: UUID,
    *,
    service: GrantService | None = None,
) -> GrantChange:
    """Block one person or team; blocking them again answers the block that stands."""
    if principal_type not in PRINCIPALS:
        raise invalid("principal_type")
    if principal_type == "user" and principal_id == server.user_id:
        raise invalid("owner")
    await lock_org_row(db, ctx.org_id)
    return await (service or GrantService()).grant(
        db,
        ctx,
        GrantTarget(RESOURCE_TYPE, tool.id),
        GrantRequest(RELATION, principal_type, principal_id),
    )


async def unblock(
    db: AsyncSession,
    ctx: OrgContext,
    tool: McpTool,
    grant_id: UUID,
    *,
    service: GrantService | None = None,
) -> GrantChange:
    """Lift a block of this tool; any other grant id is unknown (404)."""
    grant = await db.get(ResourceGrant, grant_id, populate_existing=True)
    if (
        grant is None
        or grant.org_id != ctx.org_id
        or grant.state != "active"
        or (grant.resource_type, grant.resource_id, grant.relation)
        != (RESOURCE_TYPE, tool.id, RELATION)
    ):
        raise authorization_error("not_found")
    await lock_org_row(db, ctx.org_id)
    return await (service or GrantService()).revoke(db, ctx, grant.id)


async def blocked_tool_ids(
    db: AsyncSession, org_id: UUID | None, user_id: UUID | None, tool_ids: Collection[UUID]
) -> set[UUID]:
    """The tools of ``tool_ids`` this person is blocked from, directly or through a team."""
    if not tool_ids or org_id is None or user_id is None:
        return set()
    teams = select(GroupMember.group_id).where(
        GroupMember.user_id == user_id, GroupMember.org_id == org_id
    )
    rows = await db.scalars(
        select(ResourceGrant.resource_id).where(
            *_blocks(tool_ids),
            ResourceGrant.org_id == org_id,
            or_(
                and_(ResourceGrant.principal_type == "user", ResourceGrant.principal_id == user_id),
                and_(
                    ResourceGrant.principal_type == "group",
                    ResourceGrant.principal_id.in_(teams),
                ),
            ),
        )
    )
    return set(rows.all())


async def call_refusal(
    tenant_id: UUID, org_id: UUID | None, user_id: UUID | None, tool_id: UUID
) -> str | None:
    """Why this call must not run now, read in a session of its own (plan 3.11).

    Switched off, no longer registered (approval withdrawn, or the connection changed) and
    blocked for this person are checked per call, so they stop a run already in progress.
    A tool row that is gone is left to the call itself.
    """
    from app.approvals import emergency  # the session factory the per-call checks share

    async with tenant_session(SessionTenant(tenant_id), maker=emergency.async_session) as db:
        tool = await db.get(McpTool, tool_id)
        if tool is None:
            return None
        if not tool.enabled or tool.review_state != "approved":
            return TOOL_OFF
        server = await db.get(McpServer, tool.server_id)
        if server is not None and not registered(server):
            return NOT_REGISTERED
        if await blocked_tool_ids(db, org_id, user_id, [tool_id]):
            return BLOCKED
    return None
