"""Read-only preview of a member removal (plan 10.7 "구성원 제거" (1)-(2), S15; E-02).

Lists what the leaver owns in the organization (with the E-01 transfer blockers that
would remain after the exit stops the leaver's own entry points), the grants that will be
revoked, group/role assignments that will be cleared and the triggers, Agent API
deployments and keys that will be stopped. Nothing is locked or written.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.authz.error_codes import authorization_error
from app.authz.member_exit_settlement import credential_delete_blockers, held_grants_query
from app.authz.org_administrators import active_administrators
from app.authz.org_control import OrgControlContext
from app.authz.share_targets import SHARE_TYPES, ShareRow
from app.authz.share_transfer_blocks import check_transferable
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.organization import OrganizationMember, OrganizationRoleGrant
from app.models.organization_group import GroupMember
from app.schemas.org_member_exit import HeldGrant, MemberExitPreview, OwnedResource


async def _owned_entry(db: AsyncSession, kind: str, row: ShareRow, user_id: UUID) -> OwnedResource:
    blocked: dict[str, object] | None = None
    try:
        await check_transferable(db, row, user_id, exiting=user_id)
    except AppError as error:
        if error.code != "transfer_blocked":
            raise
        blocked = dict(error.details or {})
    if isinstance(row, Credential) and (bindings := await credential_delete_blockers(db, row.id)):
        blocked = {**(blocked or {}), **bindings}  # also blocks the delete action
    return OwnedResource(
        resource_type=kind,
        resource_id=row.id,
        name=row.name,
        archived=isinstance(row, Agent) and row.archived_at is not None,
        transferable=blocked is None or "personal_account" not in blocked,
        blocked_by=blocked,
    )


async def _owned(db: AsyncSession, org_id: UUID, user_id: UUID) -> list[OwnedResource]:
    owned: list[OwnedResource] = []
    for kind in SHARE_TYPES.values():
        model = kind.model
        rows = await db.scalars(
            select(model).where(model.org_id == org_id, model.user_id == user_id).order_by(model.id)
        )
        owned += [await _owned_entry(db, kind.name, row, user_id) for row in rows]
    items = await db.scalars(  # no transfer path until I-04 (E-01): delete or unpublish
        select(MarketplaceItem)
        .where(MarketplaceItem.org_id == org_id, MarketplaceItem.owner_user_id == user_id)
        .order_by(MarketplaceItem.id)
    )
    owned += [
        OwnedResource(
            resource_type="marketplace_item",
            resource_id=item.id,
            name=item.name,
            transferable=False,
        )
        for item in items
    ]
    return owned


async def member_exit_preview(
    db: AsyncSession, context: OrgControlContext, user_id: UUID
) -> MemberExitPreview:
    org = context.organization
    member = await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org.id,
            OrganizationMember.tenant_id == org.tenant_id,
            OrganizationMember.user_id == user_id,
        )
    )
    if member is None:
        raise authorization_error("not_found")
    administrators = await active_administrators(db, org)
    grants = await db.scalars(held_grants_query(org.id, user_id).order_by(ResourceGrant.id))
    return MemberExitPreview(
        user_id=user_id,
        status=member.status,
        is_last_admin=member.status == "active" and administrators == {user_id},
        owned=await _owned(db, org.id, user_id),
        grants=[
            HeldGrant(
                grant_id=grant.id,
                resource_type=grant.resource_type,
                resource_id=grant.resource_id,
                relation=grant.relation,
            )
            for grant in grants
        ],
        group_ids=list(
            await db.scalars(
                select(GroupMember.group_id)
                .where(GroupMember.org_id == org.id, GroupMember.user_id == user_id)
                .order_by(GroupMember.group_id)
            )
        ),
        roles=list(
            await db.scalars(
                select(OrganizationRoleGrant.role)
                .where(
                    OrganizationRoleGrant.org_id == org.id,
                    OrganizationRoleGrant.principal_type == "user",
                    OrganizationRoleGrant.principal_id == user_id,
                )
                .order_by(OrganizationRoleGrant.role)
            )
        ),
        trigger_ids=await _ids(db, AgentTrigger, org.id, user_id, AgentTrigger.status == "active"),
        deployment_ids=await _ids(
            db, AgentDeployment, org.id, user_id, AgentDeployment.status == "active"
        ),
        api_key_ids=await _ids(db, AgentApiKey, org.id, user_id, AgentApiKey.revoked_at.is_(None)),
    )


async def _ids(
    db: AsyncSession,
    model: type[AgentTrigger] | type[AgentDeployment] | type[AgentApiKey],
    org_id: UUID,
    user_id: UUID,
    live: ColumnElement[bool],
) -> list[UUID]:
    found = await db.scalars(
        select(model.id)
        .where(model.org_id == org_id, model.user_id == user_id, live)
        .order_by(model.id)
    )
    return list(found)
