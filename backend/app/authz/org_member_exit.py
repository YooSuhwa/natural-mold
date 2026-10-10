"""Exit guards keep resource ownership and background entry points scoped to the org."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Uuid, column, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.org_control import OrgControlContext
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey
from app.models.agent_trigger import AgentTrigger
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationRoleGrant
from app.models.organization_group import GroupMember, OrgCapabilityGrant
from app.models.skill import Skill
from app.models.tool import Tool


def owner_queries(user_id: UUID) -> tuple[Select[tuple[UUID]], ...]:
    return (
        select(Agent.id).where(Agent.user_id == user_id),
        select(Skill.id).where(Skill.user_id == user_id),
        select(McpServer.id).where(McpServer.user_id == user_id),
        select(Tool.id).where(Tool.user_id == user_id),
        select(Credential.id).where(Credential.user_id == user_id),
        select(MarketplaceItem.id).where(MarketplaceItem.owner_user_id == user_id),
    )


async def require_no_owned_resources(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> None:
    for query in owner_queries(user_id):
        if await db.scalar(query.limit(1)) is None:
            continue
        # m81's physical scope exists before B-06 promotes the corresponding ORM mappings.
        if (
            await db.scalar(
                query.where(column("org_id", Uuid()) == context.organization.id).limit(1)
            )
            is not None
        ):
            raise authorization_error("resource_transfer_required")


async def pause_member_triggers(
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
    await pause_member_triggers(db, context, user_id)
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
