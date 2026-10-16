"""What a caller may see of an MCP server's configuration (plan 9.5).

Header and env values can carry secrets, so only the owner, or an enforced
``mcp_server#can_edit`` holder, reads them; everyone else gets the keys with
``<redacted>`` values. Legacy/shadow callers only ever see their own rows, so
nothing is masked there and no check is made.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService
from app.models.mcp_server import McpServer
from app.models.user import User

REDACTED = "<redacted>"


async def secret_readers(
    authz: AuthzService, ctx: OrgContext, servers: Sequence[McpServer]
) -> set[UUID]:
    """Servers whose header/env values the caller may read."""
    owned = {server.id for server in servers if server.user_id == ctx.user_id}
    others = [f"mcp_server:{server.id}" for server in servers if server.id not in owned]
    if not others or not authz.enforces("mcp_server"):
        return owned
    editable = await authz.filter_visible(Principal.for_user(ctx), "can_edit", others, legacy=[])
    return owned | {UUID(obj.partition(":")[2]) for obj in editable}


def masked(values: dict[str, Any] | None) -> dict[str, Any]:
    return dict.fromkeys(values or {}, REDACTED)


async def export_names(
    db: AsyncSession, ctx: OrgContext, servers: Sequence[McpServer]
) -> dict[UUID, str]:
    """Export keys: owned rows keep their name, granted rows get ``name (owner)``.

    A granted row that still collides gets the row id prefix, so it never overwrites
    another entry; owned rows keep the legacy last-wins behaviour.
    """
    foreign = {server.user_id for server in servers if server.user_id != ctx.user_id}
    owners: dict[UUID, str] = {}
    if foreign:
        rows = await db.execute(
            select(User.id, User.display_name, User.name).where(User.id.in_(foreign))
        )
        owners = {row.id: row.display_name or row.name for row in rows}
    names: dict[UUID, str] = {}
    taken: set[str] = set()
    for server in servers:
        name = server.name
        if server.user_id != ctx.user_id:
            name = f"{name} ({owners.get(server.user_id, 'shared')})"
        if name in taken and server.user_id != ctx.user_id:  # owned rows: legacy keys
            name = f"{name} [{str(server.id)[:8]}]"
        taken.add(name)
        names[server.id] = name
    return names
