"""Manual invitation joins cannot override a company or organization suspension."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
)
from app.models.organization_group import Group, GroupMember, OrgInvitation
from app.models.user import User
from app.services.orgs.versioning import utc_timestamp

GROUP_IDS: Final = TypeAdapter(tuple[UUID, ...])


@dataclass(frozen=True, slots=True)
class InvitationMembership:
    organization: Organization
    invitation: OrgInvitation
    user: User


@dataclass(frozen=True, slots=True)
class JoinedFacts:
    membership: InvitationMembership
    member_added: bool
    role_added: bool
    groups_added: tuple[UUID, ...]


async def join_invited_membership(db: AsyncSession, target: InvitationMembership) -> JoinedFacts:
    organization, invitation, user = target.organization, target.invitation, target.user
    company = await db.get(TenantMember, (organization.tenant_id, user.id), populate_existing=True)
    member = await db.get(OrganizationMember, (organization.id, user.id), populate_existing=True)
    if company is not None and company.status != "active":
        raise authorization_error("invitation_membership_blocked")
    if member is not None:
        if member.status == "suspended":
            raise authorization_error("invitation_membership_blocked")
        if member.removed_at is not None and utc_timestamp(invitation.created_at) <= utc_timestamp(
            member.removed_at
        ):
            raise authorization_error("invitation_invalid")
    group_ids = GROUP_IDS.validate_python(invitation.group_ids)
    groups = list(
        (
            await db.scalars(
                select(Group).where(
                    Group.id.in_(group_ids),
                    Group.org_id == organization.id,
                    Group.tenant_id == organization.tenant_id,
                )
            )
        ).all()
    )
    if {group.id for group in groups} != set(group_ids):
        raise authorization_error("invitation_group_unavailable")
    if any(group.source != "manual" for group in groups):
        raise authorization_error("policy_locked")
    if company is None:
        db.add(TenantMember(tenant_id=organization.tenant_id, user_id=user.id))
    member_added = member is None or member.status != "active"
    if member is None:
        member = OrganizationMember(
            org_id=organization.id,
            tenant_id=organization.tenant_id,
            user_id=user.id,
            status="active",
            invited_by=invitation.invited_by,
        )
        db.add(member)
    elif member_added:
        member.status, member.removed_at = "active", None
        member.joined_at = datetime.now(UTC)
        member.invited_by = invitation.invited_by
    await db.flush()
    role_added = False
    if invitation.role != "member":
        grant = await db.scalar(
            select(OrganizationRoleGrant.id).where(
                OrganizationRoleGrant.org_id == organization.id,
                OrganizationRoleGrant.tenant_id == organization.tenant_id,
                OrganizationRoleGrant.principal_type == "user",
                OrganizationRoleGrant.principal_id == user.id,
                OrganizationRoleGrant.role == invitation.role,
            )
        )
        if grant is None:
            db.add(
                OrganizationRoleGrant(
                    org_id=organization.id,
                    tenant_id=organization.tenant_id,
                    principal_type="user",
                    principal_id=user.id,
                    role=invitation.role,
                    granted_by=invitation.invited_by,
                )
            )
            role_added = True
    added_groups: list[UUID] = []
    for group_id in group_ids:
        if await db.get(GroupMember, (group_id, user.id)) is None:
            db.add(
                GroupMember(
                    group_id=group_id,
                    user_id=user.id,
                    org_id=organization.id,
                    tenant_id=organization.tenant_id,
                )
            )
            added_groups.append(group_id)
    return JoinedFacts(target, member_added, role_added, tuple(added_groups))
