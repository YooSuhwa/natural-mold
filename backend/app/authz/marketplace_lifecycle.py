"""Marketplace lifecycle hooks and install capabilities (plan 7.2.1, 9.6, D-05).

* Publishing queues the item's org/owner tuples (``on_resource_created``). A publish may
  reuse an existing item (same owner + slug); the queued writes are then the tuples the
  item already has, and OpenFGA writes are idempotent (duplicates ignored).
* Installing creates the installer's own copy (skill, MCP server and its tools, agent);
  its tuples are queued in the same transaction so an enforced installer can use it.
* Installing needs the created resource type's ``cap_*`` (9.6 "설치 시 만들어지는 자원
  유형의 cap_* 필요"); every member holds it today (legacy allow).

Items are never deleted and their owner never changes through these routes; a future
delete/transfer path calls ``on_resource_deleted`` / ``on_owner_changed`` directly.
Every hook is a no-op in legacy mode.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import pending_change
from app.authz.org_context import OrgContext
from app.authz.resource_gate import gate_cap
from app.authz.resource_lifecycle import (
    LifecycleChange,
    LifecycleRow,
    on_resource_created,
    on_resource_deleted,
)
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill

INSTALL_CAPS = {"skill": "cap_create_skill", "mcp": "cap_register_mcp", "agent": "cap_create_agent"}


def merge(*changes: LifecycleChange) -> LifecycleChange:
    return LifecycleChange(tuple(i for change in changes for i in change.outbox_ids))


async def item_published(
    db: AsyncSession, authz: AuthzService, item: MarketplaceItem
) -> LifecycleChange:
    return await on_resource_created(db, item, mode=authz.mode)


def install_caps(
    authz: AuthzService, ctx: OrgContext
) -> Callable[[MarketplaceItem], Awaitable[None]]:
    """``ItemGate.then`` for install: the created type's ``cap_*`` on the authorized row.

    Running after the item decision keeps hidden and missing items the same 404 (no
    capability oracle).
    """

    async def check(item: MarketplaceItem) -> None:
        if item.resource_type in INSTALL_CAPS:
            await gate_cap(authz, ctx, INSTALL_CAPS[item.resource_type])

    return check


async def _installed_rows(
    db: AsyncSession, installation: MarketplaceInstallation, ctx: OrgContext
) -> list[Skill | McpServer | Agent]:
    """The installer's own copies (owner and organization of ``ctx``)."""
    rows: list[Skill | McpServer | Agent] = []
    for model, rid in (
        (Skill, installation.installed_skill_id),
        (McpServer, installation.installed_mcp_server_id),
        (Agent, installation.installed_agent_id),
    ):
        row = await db.get(model, rid) if rid is not None else None
        if row is not None and (row.user_id, row.org_id) == (ctx.user_id, ctx.org_id):
            rows.append(row)
    return rows


async def installation_created(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, installation: MarketplaceInstallation
) -> LifecycleChange:
    """Queue the tuples of the resources an install (or new-copy update) created."""
    if authz.mode == "legacy":
        return LifecycleChange()
    await db.flush()
    rows: list[LifecycleRow] = list(await _installed_rows(db, installation, ctx))
    if any(isinstance(row, McpServer) for row in rows):
        tools = await db.scalars(
            select(McpTool).where(McpTool.server_id == installation.installed_mcp_server_id)
        )
        rows += list(tools)
    created = [await on_resource_created(db, row, mode=authz.mode) for row in rows]
    return pending_change(db, *created)  # plus F-02 revocations of a snapshot refresh


async def installation_resources_deleting(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, installation_id: UUID
) -> LifecycleChange:
    """Before ``delete_resource=True`` removes the copies: queue their tuple deletes."""
    if authz.mode == "legacy":
        return LifecycleChange()
    installation = await db.get(MarketplaceInstallation, installation_id)
    if installation is None or (installation.user_id, installation.org_id) != (
        ctx.user_id,
        ctx.org_id,
    ):
        return LifecycleChange()  # the service answers 404
    rows = await _installed_rows(db, installation, ctx)
    return merge(*[await on_resource_deleted(db, row, mode=authz.mode) for row in rows])
