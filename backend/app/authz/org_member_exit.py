"""Exit guards keep resource ownership and background entry points scoped to the org."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Uuid, column, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.org_administrators import active_administrators
from app.authz.org_control import OrgControlContext
from app.authz.tenant_org_write_locks import lock_member_rows
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationMember, OrganizationRoleGrant
from app.models.organization_group import GroupMember, OrgCapabilityGrant
from app.models.skill import Skill
from app.models.tool import Tool


@dataclass(frozen=True, slots=True)
class MemberExitPlan:
    context: OrgControlContext
    member: OrganizationMember


ExitSettlement = Callable[[], Awaitable[None]]  # E-02 writes between the member and resource locks


def owner_queries(
    user_id: UUID, *, include_archived: bool = True
) -> tuple[tuple[str, Select[tuple[UUID]]], ...]:
    """Restricted owner FKs by resource type. An organization exit leaves archived agents
    (trash, 10.7 "보관") with their owner; account erasure counts them (m83 RESTRICT)."""
    agents = select(Agent.id).where(Agent.user_id == user_id)
    return (
        ("agent", agents if include_archived else agents.where(Agent.archived_at.is_(None))),
        ("skill", select(Skill.id).where(Skill.user_id == user_id)),
        ("mcp_server", select(McpServer.id).where(McpServer.user_id == user_id)),
        ("tool", select(Tool.id).where(Tool.user_id == user_id)),
        ("credential", select(Credential.id).where(Credential.user_id == user_id)),
        (
            "marketplace_item",
            select(MarketplaceItem.id).where(MarketplaceItem.owner_user_id == user_id),
        ),
    )


async def require_no_owned_resources(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    """409 ``resource_transfer_required`` listing the org resources the member still owns."""
    remaining: dict[str, list[str]] = {}
    for kind, query in owner_queries(user_id, include_archived=False):
        if await db.scalar(query.limit(1)) is None:
            continue
        # m81's physical scope exists before B-06 promotes the corresponding ORM mappings.
        owned = await db.scalars(
            query.where(column("org_id", Uuid()) == context.organization.id)
            .order_by(column("id", Uuid()))
            .with_for_update()
        )
        if ids := [str(value) for value in owned]:
            remaining[kind] = ids
    if remaining:
        raise authorization_error("resource_transfer_required", {"resources": remaining})


async def lock_member_entry_points(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    """Lock resource parents before trigger and API-key children."""
    await db.scalars(
        select(Agent)
        .where(
            Agent.tenant_id == context.organization.tenant_id,
            Agent.org_id == context.organization.id,
        )
        .order_by(Agent.id)
        .with_for_update()
    )
    await db.scalars(
        select(AgentTrigger)
        .where(
            AgentTrigger.tenant_id == context.organization.tenant_id,
            AgentTrigger.org_id == context.organization.id,
            AgentTrigger.user_id == user_id,
        )
        .order_by(AgentTrigger.id)
        .with_for_update()
    )
    await db.scalars(
        select(AgentDeployment)
        .where(
            AgentDeployment.tenant_id == context.organization.tenant_id,
            AgentDeployment.org_id == context.organization.id,
            AgentDeployment.user_id == user_id,
        )
        .order_by(AgentDeployment.id)
        .with_for_update()
    )
    await db.scalars(
        select(AgentApiKey)
        .where(
            AgentApiKey.tenant_id == context.organization.tenant_id,
            AgentApiKey.org_id == context.organization.id,
            AgentApiKey.user_id == user_id,
        )
        .order_by(AgentApiKey.id)
        .with_for_update()
    )


async def preflight_member_exit(
    db: AsyncSession,
    context: OrgControlContext,
    user_id: UUID,
    *,
    settle: ExitSettlement | None = None,
) -> MemberExitPlan:
    """Validate one organization exit; only ``settle`` (E-02 transfers and grant
    revocations) writes, after the member checks and before the resource locks so the
    order stays organization → grants → resource."""
    await lock_member_rows(
        db,
        context.organization.tenant_id,
        context.organization.id,
        user_id,
    )
    member = await db.scalar(
        select(OrganizationMember)
        .where(
            OrganizationMember.org_id == context.organization.id,
            OrganizationMember.tenant_id == context.organization.tenant_id,
            OrganizationMember.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise authorization_error("not_found")
    administrators = await active_administrators(db, context.organization)
    if member.status == "active" and user_id in administrators and len(administrators) == 1:
        raise authorization_error("last_admin")
    if settle is not None:
        await settle()
    await require_no_owned_resources(db, context, user_id)
    await lock_member_entry_points(db, context, user_id)
    return MemberExitPlan(context, member)


async def pause_member_triggers(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    await lock_member_entry_points(db, context, user_id)
    await pause_locked_member_triggers(db, context, user_id)


async def pause_locked_member_triggers(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    if (
        await db.scalar(select(AgentTrigger.id).where(AgentTrigger.user_id == user_id).limit(1))
        is not None
    ):
        await db.execute(
            update(AgentTrigger)
            .where(
                AgentTrigger.user_id == user_id,
                column("org_id", Uuid()) == context.organization.id,
            )
            .values(status="paused", next_run_at=None)
        )


async def stop_member_entry_points(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    await lock_member_entry_points(db, context, user_id)
    await stop_locked_member_entry_points(db, context, user_id)


async def stop_locked_member_entry_points(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    await pause_locked_member_triggers(db, context, user_id)
    if (
        await db.scalar(select(AgentApiKey.id).where(AgentApiKey.user_id == user_id).limit(1))
        is not None
    ):
        await db.execute(
            update(AgentApiKey)
            .where(
                AgentApiKey.user_id == user_id,
                column("org_id", Uuid()) == context.organization.id,
                AgentApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC).replace(tzinfo=None))
        )
    await db.execute(  # owner-bound Agent API entry points (10.7 item 4)
        update(AgentDeployment)
        .where(
            AgentDeployment.user_id == user_id,
            AgentDeployment.org_id == context.organization.id,
            AgentDeployment.status == "active",
        )
        .values(status="disabled", updated_at=datetime.now(UTC).replace(tzinfo=None))
    )


async def clear_member_assignments(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    await db.execute(
        delete(GroupMember).where(
            GroupMember.org_id == context.organization.id,
            GroupMember.tenant_id == context.organization.tenant_id,
            GroupMember.user_id == user_id,
        )
    )
    await db.execute(
        delete(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == context.organization.id,
            OrganizationRoleGrant.tenant_id == context.organization.tenant_id,
            OrganizationRoleGrant.principal_type == "user",
            OrganizationRoleGrant.principal_id == user_id,
        )
    )
    await db.execute(
        delete(OrgCapabilityGrant).where(
            OrgCapabilityGrant.org_id == context.organization.id,
            OrgCapabilityGrant.tenant_id == context.organization.tenant_id,
            OrgCapabilityGrant.principal_type == "user",
            OrgCapabilityGrant.principal_id == user_id,
        )
    )


async def apply_member_exit(db: AsyncSession, plan: MemberExitPlan) -> None:
    """Apply a preflighted organization exit without committing."""
    plan.member.status = "removed"
    plan.member.removed_at = datetime.now(UTC)
    await clear_member_assignments(db, plan.context, plan.member.user_id)
    await stop_locked_member_entry_points(db, plan.context, plan.member.user_id)
