"""Load a full ``tuple_rows.Snapshot`` from every 7.2 source table (C-07 bootstrap/reconcile).

Rows are streamed column-wise (``yield_per``), never as ORM entities, so a large installation
does not build an identity map. ``tenant_id`` narrows every company-scoped table to one
company; ``PLATFORM`` loads only ``tenant_id IS NULL`` rows (system resources, MCP tools of
system servers). The model catalog is always loaded because ``llm_model`` fanout admission
needs it. Grants load ``state='active'`` only: ``revoking``/``revoked`` rows project nothing
(8.6 item 8).
"""

from collections.abc import Callable
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.models.skill import Skill
from app.models.tool import Tool

YIELD_PER = 1000
PLATFORM: Final = "platform"
Scope = UUID | Literal["platform"] | None


async def _stream[T](
    db: AsyncSession,
    query: Select[Any],
    tenant_column: Any,
    tenant: Scope,
    make: Callable[..., T],
) -> tuple[T, ...]:
    if tenant == PLATFORM:
        query = query.where(tenant_column.is_(None))
    elif tenant is not None:
        query = query.where(tenant_column == tenant)
    result = await db.stream(query.execution_options(yield_per=YIELD_PER))
    return tuple([make(*row) async for row in result])


def _resource(kind: str) -> Callable[..., rows.ResourceRow]:
    def make(rid: UUID, org: UUID | None, owner: UUID | None, system: bool) -> rows.ResourceRow:
        return rows.ResourceRow(kind, rid, org, owner, is_system=bool(system))

    return make


def _grant(*values: Any) -> rows.GrantRow:
    return rows.GrantRow(*values)


async def _resources(db: AsyncSession, tenant: Scope) -> tuple[rows.ResourceRow, ...]:
    agents = await _stream(
        db,
        select(Agent.id, Agent.org_id, Agent.user_id, Agent.archived_at),
        Agent.tenant_id,
        tenant,
        lambda i, o, u, archived: rows.ResourceRow("agent", i, o, u, archived_at=archived),
    )
    found: list[rows.ResourceRow] = list(agents)
    for kind, model in (("skill", Skill), ("tool", Tool), ("mcp_server", McpServer)):
        query = select(model.id, model.org_id, model.user_id, model.is_system)
        found += await _stream(db, query, model.tenant_id, tenant, _resource(kind))
    return tuple(found)


async def load_snapshot(db: AsyncSession, *, tenant_id: Scope = None) -> rows.Snapshot:
    """Every 7.2 source row: store-wide, one company, or ``PLATFORM`` (``tenant_id IS NULL``).

    Sizing: a company's rows are held as small slotted rows (~100-200 B each) only while its
    partition is diffed; store-wide runs iterate companies (``reconcile.py``).
    """
    t = tenant_id
    tools = select(McpTool.id, McpTool.server_id).join(McpServer, McpServer.id == McpTool.server_id)
    grant_columns = (
        ResourceGrant.id,
        ResourceGrant.org_id,
        ResourceGrant.resource_type,
        ResourceGrant.resource_id,
        ResourceGrant.relation,
        ResourceGrant.principal_type,
        ResourceGrant.principal_id,
        ResourceGrant.granted_at,
        ResourceGrant.state,
        ResourceGrant.grant_kind,
        ResourceGrant.condition_name,
        ResourceGrant.condition_context,
        ResourceGrant.expires_at,
        ResourceGrant.tenant_id,
    )
    item = MarketplaceItem
    return rows.Snapshot(
        tenant_members=await _stream(
            db,
            select(TenantMember.tenant_id, TenantMember.user_id, TenantMember.status),
            TenantMember.tenant_id,
            t,
            rows.TenantMemberRow,
        ),
        tenant_roles=await _stream(
            db,
            select(TenantRoleGrant.tenant_id, TenantRoleGrant.user_id, TenantRoleGrant.role),
            TenantRoleGrant.tenant_id,
            t,
            rows.TenantRoleRow,
        ),
        organizations=await _stream(
            db,
            select(Organization.id, Organization.tenant_id, Organization.status),
            Organization.tenant_id,
            t,
            rows.OrganizationRow,
        ),
        org_members=await _stream(
            db,
            select(
                OrganizationMember.org_id, OrganizationMember.user_id, OrganizationMember.status
            ),
            OrganizationMember.tenant_id,
            t,
            rows.OrgMemberRow,
        ),
        org_roles=await _stream(
            db,
            select(
                OrganizationRoleGrant.org_id,
                OrganizationRoleGrant.role,
                OrganizationRoleGrant.principal_type,
                OrganizationRoleGrant.principal_id,
            ),
            OrganizationRoleGrant.tenant_id,
            t,
            rows.OrgRoleRow,
        ),
        groups=await _stream(db, select(Group.id, Group.org_id), Group.tenant_id, t, rows.GroupRow),
        group_members=await _stream(
            db,
            select(GroupMember.group_id, GroupMember.org_id, GroupMember.user_id),
            GroupMember.tenant_id,
            t,
            rows.GroupMemberRow,
        ),
        capabilities=await _stream(
            db,
            select(
                OrgCapabilityGrant.org_id,
                OrgCapabilityGrant.capability,
                OrgCapabilityGrant.principal_type,
                OrgCapabilityGrant.principal_id,
            ),
            OrgCapabilityGrant.tenant_id,
            t,
            rows.CapabilityRow,
        ),
        resources=await _resources(db, t),
        credentials=await _stream(
            db,
            select(
                Credential.id,
                Credential.org_id,
                Credential.tenant_id,
                Credential.user_id,
                Credential.scope,
                Credential.is_system,
            ),
            Credential.tenant_id,
            t,
            rows.CredentialRow,
        ),
        marketplace_items=await _stream(
            db,
            select(
                item.id,
                item.org_id,
                item.tenant_id,
                item.owner_user_id,
                item.is_system,
                item.tenant_publication,
            ),
            item.tenant_id,
            t,
            rows.MarketplaceItemRow,
        ),
        mcp_tools=await _stream(db, tools, McpServer.tenant_id, t, rows.McpToolRow),
        models=await _stream(db, select(Model.id, Model.is_visible), None, None, rows.ModelRow),
        grants=await _stream(
            db,
            select(*grant_columns).where(ResourceGrant.state == "active"),
            ResourceGrant.tenant_id,
            t,
            _grant,
        ),
    )
