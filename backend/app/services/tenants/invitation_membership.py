"""Company-only membership created by a company invitation."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.membership_sync import capture_user
from app.models.organization import TenantMember, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from app.tenancy.limit_capacity import require_member_capacity


@dataclass(frozen=True, slots=True)
class TenantInvitationMembership:
    invitation: TenantInvitation
    user: User


@dataclass(frozen=True, slots=True)
class TenantJoinedFacts:
    target: TenantInvitationMembership
    member_added: bool
    role_added: bool


async def join_tenant_invitation(
    db: AsyncSession, target: TenantInvitationMembership
) -> TenantJoinedFacts:
    invitation, user = target.invitation, target.user
    with db.no_autoflush:
        member = await db.scalar(
            select(TenantMember)
            .where(TenantMember.tenant_id == invitation.tenant_id, TenantMember.user_id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if member is not None and member.status == "removed":
            raise authorization_error("invitation_membership_blocked")
        member_added = member is None or member.status != "active"
        sync = await capture_user(db, invitation.tenant_id, user.id, None)
        if member is None:
            await require_member_capacity(db, invitation.tenant_id)  # H-06 member_limit
            db.add(TenantMember(tenant_id=invitation.tenant_id, user_id=user.id))
        elif member.status == "suspended":
            member.status = "active"
        role_added = False
        if invitation.role is None:
            await sync.queue(db)
            return TenantJoinedFacts(target, member_added, role_added)
        role = await db.scalar(
            select(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == invitation.tenant_id,
                TenantRoleGrant.user_id == user.id,
                TenantRoleGrant.role == invitation.role,
            )
            .with_for_update()
        )
        if role is None:
            db.add(
                TenantRoleGrant(
                    tenant_id=invitation.tenant_id,
                    user_id=user.id,
                    role=invitation.role,
                    granted_by=invitation.created_by,
                )
            )
            role_added = True
        await sync.queue(db)
        return TenantJoinedFacts(target, member_added, role_added)
