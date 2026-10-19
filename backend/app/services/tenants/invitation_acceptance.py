"""One-use company invitation acceptance under company-first locks."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.models.organization import Tenant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from app.schemas.invitation_accept import InvitationAccepted
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.tenants.invitation_membership import (
    TenantInvitationMembership,
    TenantJoinedFacts,
    join_tenant_invitation,
)
from app.services.tenants.invitation_queries import locked_invitation_by_digest
from app.services.tenants.versioning import utc_timestamp
from app.tenancy.context import ensure_tenant_active


@dataclass(frozen=True, slots=True)
class TenantInvitationAcceptance:
    mutation: OrgMutationRequest
    user_id: UUID
    token: SecretStr


@dataclass(frozen=True, slots=True)
class RegistrationTenantInvitationAcceptance:
    mutation: OrgMutationRequest
    tenant: Tenant
    invitation: TenantInvitation
    user: User
    token: SecretStr


def require_pending_tenant_invitation(invitation: TenantInvitation, email: str) -> None:
    if invitation.status == "revoked":
        raise authorization_error("tenant_invitation_revoked")
    if invitation.status == "accepted":
        raise authorization_error("tenant_invitation_used")
    if utc_timestamp(invitation.expires_at) <= datetime.now(UTC):
        raise authorization_error("tenant_invitation_expired")
    if invitation.email.casefold() != email.casefold():
        raise authorization_error("tenant_invitation_email_mismatch")


async def accept_tenant_invitation(
    attempt: TenantInvitationAcceptance,
) -> InvitationAccepted:
    db = attempt.mutation.db
    digest = hashlib.sha256(attempt.token.get_secret_value().encode()).hexdigest()
    with db.no_autoflush:
        tenant, invitation = await locked_invitation_by_digest(db, digest)
        user = await db.scalar(
            select(User).where(User.id == attempt.user_id).execution_options(populate_existing=True)
        )
        if user is None or not user.is_active:
            raise authorization_error("tenant_invitation_invalid")
        return await apply_locked_tenant_acceptance(
            attempt.mutation, tenant, invitation, user, digest
        )


async def accept_registration_tenant_invitation(
    attempt: RegistrationTenantInvitationAcceptance,
) -> InvitationAccepted:
    digest = hashlib.sha256(attempt.token.get_secret_value().encode()).hexdigest()
    return await apply_locked_tenant_acceptance(
        attempt.mutation,
        attempt.tenant,
        attempt.invitation,
        attempt.user,
        digest,
    )


async def apply_locked_tenant_acceptance(
    mutation: OrgMutationRequest,
    tenant: Tenant,
    invitation: TenantInvitation,
    user: User,
    digest: str,
) -> InvitationAccepted:
    if invitation.tenant_id != tenant.id or not secrets.compare_digest(
        invitation.token_hash, digest
    ):
        raise authorization_error("tenant_invitation_invalid")
    if not user.is_active:
        raise authorization_error("tenant_invitation_invalid")
    require_pending_tenant_invitation(invitation, user.email)
    ensure_tenant_active(tenant)
    facts = await join_tenant_invitation(mutation.db, TenantInvitationMembership(invitation, user))
    invitation.status = "accepted"
    invitation.accepted_at, invitation.accepted_by = datetime.now(UTC), user.id
    await record_tenant_acceptance(mutation, facts)
    await mutation.db.flush()
    return InvitationAccepted(org_id=None, tenant_id=tenant.id, user_id=user.id)


async def record_tenant_acceptance(mutation: OrgMutationRequest, facts: TenantJoinedFacts) -> None:
    invitation, user = facts.target.invitation, facts.target.user
    events = [(audit_actions.TENANT_INVITATION_ACCEPT, "tenant_invitation", invitation.id)]
    if facts.member_added:
        events.append((audit_actions.TENANT_MEMBER_ADD, "tenant_member", user.id))
    if facts.role_added:
        events.append((audit_actions.TENANT_ROLE_GRANT, "tenant_role_grant", user.id))
    for action, target_type, target_id in events:
        await audit_service.record_event(
            mutation.db,
            actor_type="user",
            actor_user_id=user.id,
            actor_email_snapshot=user.email,
            owner_user_id=user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome="success",
            request=mutation.request,
            metadata={"tenant_id": str(invitation.tenant_id), "source": "invitation"},
        )
