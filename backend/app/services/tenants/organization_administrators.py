"""Initial organization administrators join the caller's create transaction."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_org_invitation_token
from app.authz import audit_actions
from app.authz.org_control import OrgControlContext
from app.authz.org_member_queries import company_member_target
from app.authz.tenant_org_write_locks import lock_member_rows
from app.dependencies import CurrentUser
from app.models.organization import Organization, OrganizationMember, OrganizationRoleGrant
from app.models.organization_group import OrgInvitation
from app.schemas.org_invitations import InvitationCreated
from app.schemas.tenant_organizations import (
    TenantOrganizationAdministrator,
    TenantOrganizationAdministratorResult,
    TenantOrganizationInvitationAdministrator,
    TenantOrganizationMemberAdministrator,
    TenantOrganizationNoAdministrator,
)
from app.services import audit_service


async def assign_initial_administrator(
    db: AsyncSession,
    request: Request,
    organization: Organization,
    actor: CurrentUser,
    selected: TenantOrganizationAdministrator | None,
) -> TenantOrganizationAdministratorResult:
    """Add one selected administrator without committing or relocking a parent row."""
    if selected is None:
        return TenantOrganizationNoAdministrator()
    context = OrgControlContext(
        organization=organization,
        actor=actor,
        permissions=("can_manage_members", "can_manage_policy"),
    )
    match selected.user_id, selected.email:
        case user_id, None if user_id is not None:
            await lock_member_rows(db, organization.tenant_id, organization.id, user_id)
            await company_member_target(db, context, user_id)
            db.add(
                OrganizationMember(
                    org_id=organization.id,
                    tenant_id=organization.tenant_id,
                    user_id=user_id,
                    status="active",
                    invited_by=actor.id,
                )
            )
            db.add(
                OrganizationRoleGrant(
                    org_id=organization.id,
                    tenant_id=organization.tenant_id,
                    principal_type="user",
                    principal_id=user_id,
                    role="admin",
                    granted_by=actor.id,
                )
            )
            await _record_member_audits(db, request, organization, actor, user_id)
            return TenantOrganizationMemberAdministrator(user_id=user_id)
        case None, email if email is not None:
            return await _invite_administrator(db, request, organization, actor, str(email))
        case _:
            raise ValueError("administrator selection requires exactly one target")


async def _record_member_audits(
    db: AsyncSession,
    request: Request,
    organization: Organization,
    actor: CurrentUser,
    user_id: UUID,
) -> None:
    for action in (audit_actions.ORG_MEMBER_ADD, audit_actions.ORG_ROLE_GRANT):
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=actor.id,
            actor_email_snapshot=actor.email,
            owner_user_id=actor.id,
            action=action,
            target_type="organization_member",
            target_id=str(user_id),
            outcome="success",
            request=request,
            metadata={"org_id": str(organization.id), "role": "admin"},
        )


async def _invite_administrator(
    db: AsyncSession,
    request: Request,
    organization: Organization,
    actor: CurrentUser,
    email: str,
) -> TenantOrganizationInvitationAdministrator:
    now, invitation_id = datetime.now(UTC), uuid4()
    token = create_org_invitation_token(invitation_id)
    row = OrgInvitation(
        id=invitation_id,
        org_id=organization.id,
        tenant_id=organization.tenant_id,
        email=email.casefold(),
        role="admin",
        group_ids=[],
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_at=now,
        expires_at=now + timedelta(days=7),
        invited_by=actor.id,
    )
    db.add(row)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.id,
        actor_email_snapshot=actor.email,
        owner_user_id=actor.id,
        action=audit_actions.ORG_INVITATION_CREATE,
        target_type="org_invitation",
        target_id=row.id,
        target_name_snapshot=row.email,
        outcome="success",
        request=request,
        metadata={"org_id": str(organization.id), "role": "admin", "group_count": 0},
    )
    invitation = InvitationCreated(
        id=row.id,
        email=row.email,
        role="admin",
        group_ids=(),
        expires_at=row.expires_at,
        created_at=row.created_at,
        status="pending",
        token=token,
    )
    return TenantOrganizationInvitationAdministrator(invitation=invitation)
