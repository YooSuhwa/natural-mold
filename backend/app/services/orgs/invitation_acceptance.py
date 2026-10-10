"""Accept once under org/company locks, with fresh account/email and status checks."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue, SecretStr
from sqlalchemy import select

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.invitation_queries import locked_invitation_scope
from app.models.organization_group import OrgInvitation
from app.models.user import User
from app.schemas.invitation_accept import InvitationAccepted
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_membership import (
    InvitationMembership,
    JoinedFacts,
    join_invited_membership,
)
from app.services.orgs.versioning import bump_version, utc_timestamp
from app.tenancy.context import ensure_tenant_active


@dataclass(frozen=True, slots=True)
class InvitationAcceptance:
    mutation: OrgMutationRequest
    user_id: UUID
    token: SecretStr


def require_pending_invitation(invitation: OrgInvitation, email: str) -> None:
    if invitation.revoked_at is not None:
        raise authorization_error("invitation_invalid")
    if invitation.accepted_at is not None:
        raise authorization_error("invitation_used")
    if utc_timestamp(invitation.expires_at) <= datetime.now(UTC):
        raise authorization_error("invitation_expired")
    if invitation.email.casefold() != email.casefold():
        raise authorization_error("invitation_email_mismatch")


async def accept_invitation(attempt: InvitationAcceptance) -> InvitationAccepted:
    db = attempt.mutation.db
    digest = hashlib.sha256(attempt.token.get_secret_value().encode()).hexdigest()
    organization, tenant, invitation = await locked_invitation_scope(db, digest)
    user = await db.scalar(
        select(User).where(User.id == attempt.user_id).execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise authorization_error("invitation_invalid")
    require_pending_invitation(invitation, user.email)
    ensure_tenant_active(tenant)
    if organization.status != "active":
        raise authorization_error("organization_unavailable")
    facts = await join_invited_membership(db, InvitationMembership(organization, invitation, user))
    invitation.accepted_at, invitation.accepted_by = datetime.now(UTC), user.id
    user.last_active_org_id = organization.id
    bump_version(organization)
    await record_acceptance(attempt.mutation, facts)
    await db.flush()
    return InvitationAccepted(org_id=organization.id, tenant_id=tenant.id, user_id=user.id)


async def record_acceptance(mutation: OrgMutationRequest, facts: JoinedFacts) -> None:
    target = facts.membership
    org, invitation, user = target.organization, target.invitation, target.user
    events: list[tuple[str, str, UUID, dict[str, JsonValue]]] = [
        (
            audit_actions.ORG_INVITATION_ACCEPT,
            "org_invitation",
            invitation.id,
            {"role": invitation.role, "group_count": len(invitation.group_ids)},
        )
    ]
    if facts.member_added:
        events.append(
            (audit_actions.ORG_MEMBER_ADD, "organization_member", user.id, {"source": "invitation"})
        )
    if facts.company_member_added:
        events.append(
            (audit_actions.TENANT_MEMBER_ADD, "tenant_member", user.id, {"source": "invitation"})
        )
    if facts.role_added:
        events.append(
            (
                audit_actions.ORG_ROLE_GRANT,
                "organization",
                org.id,
                {
                    "principal_type": "user",
                    "principal_id": str(user.id),
                    "role": invitation.role,
                    "source": "invitation",
                },
            )
        )
    events.extend(
        (
            audit_actions.GROUP_MEMBER_ADD,
            "group",
            key,
            {"user_id": str(user.id), "source": "invitation"},
        )
        for key in facts.groups_added
    )
    for action, target_type, target_id, details in events:
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
            metadata={
                **details,
                "org_id": str(org.id),
                "tenant_id": str(org.tenant_id),
                "authz_epoch": org.authz_epoch,
            },
        )
