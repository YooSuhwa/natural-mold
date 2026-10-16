"""ORM rows -> ``tuple_rows`` snapshots (plan 7.2); shared by the outbox (C-04) and reconcile.

Loaders only map columns; projection rules stay in ``tuples.py``.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import Tool

_SCOPED = {
    "agent": Agent,
    "skill": Skill,
    "tool": Tool,
    "mcp_server": McpServer,
    "credential": Credential,
    "marketplace_item": MarketplaceItem,
}


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """Where a shareable object lives; ``None`` org/tenant is a platform/company row."""

    org_id: UUID | None
    tenant_id: UUID | None
    archived: bool = False


def grant_row(grant: ResourceGrant) -> rows.GrantRow:
    return rows.GrantRow(
        id=grant.id,
        org_id=grant.org_id,
        resource_type=grant.resource_type,
        resource_id=grant.resource_id,
        relation=grant.relation,
        principal_type=grant.principal_type,
        principal_id=grant.principal_id,
        granted_at=grant.granted_at,
        state=grant.state,
        grant_kind=grant.grant_kind,
        condition_name=grant.condition_name,
        condition_context=grant.condition_context,
        expires_at=grant.expires_at,
        tenant_id=grant.tenant_id,
    )


async def active_grant_rows(
    db: AsyncSession, resource_type: str, resource_id: UUID, *, exclude: UUID | None = None
) -> list[rows.GrantRow]:
    """Every active grant on one object (all organizations visible to the session)."""
    query = select(ResourceGrant).where(
        ResourceGrant.resource_type == resource_type,
        ResourceGrant.resource_id == resource_id,
        ResourceGrant.state == "active",
    )
    if exclude is not None:
        query = query.where(ResourceGrant.id != exclude)
    found = await db.scalars(query.order_by(ResourceGrant.id))
    return [grant_row(grant) for grant in found]


async def marketplace_item_row(db: AsyncSession, item_id: UUID) -> rows.MarketplaceItemRow | None:
    """The item row; its ``tenant`` tuple shares a key with organization fanout grants.

    ``tenant_publication`` is loaded from the row: ``approved`` adds the company
    ``installer`` tuple (E-09); the shared ``tenant`` key is projected in every state.
    """
    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        return None
    return rows.MarketplaceItemRow(
        id=item.id,
        org_id=item.org_id,
        tenant_id=item.tenant_id,
        owner_id=item.owner_user_id,
        is_system=item.is_system,
        tenant_publication=item.tenant_publication or "none",
    )


async def resource_scope(
    db: AsyncSession, resource_type: str, resource_id: UUID
) -> ResourceScope | None:
    """The stored scope of a grant target; types without a table yet are unknown (``None``)."""
    if resource_type == "llm_model":  # platform catalog row, shared by fanout only
        found = await db.scalar(select(Model.id).where(Model.id == resource_id))
        return None if found is None else ResourceScope(None, None)
    if resource_type == "mcp_tool":
        server = await db.scalar(select(McpTool.server_id).where(McpTool.id == resource_id))
        return None if server is None else await resource_scope(db, "mcp_server", server)
    model = _SCOPED.get(resource_type)
    if model is None:
        return None
    row = await db.get(model, resource_id)
    if row is None:
        return None
    archived = isinstance(row, Agent) and row.archived_at is not None
    return ResourceScope(row.org_id, row.tenant_id, archived)
