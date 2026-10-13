"""Installation/company/org restrictions intersect; no narrower scope can reopen signup."""

import hashlib
from dataclasses import dataclass
from typing import Final

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.registration_queries import (
    RegistrationOrganization,
    locked_registration_scope,
)
from app.config import settings
from app.models.organization_group import OrgInvitation
from app.schemas.auth import RegisterRequest
from app.schemas.org import OrganizationSettings, RegistrationPolicy
from app.services.orgs.invitation_acceptance import require_pending_invitation
from app.tenancy.context import ensure_tenant_active

POLICY: Final = TypeAdapter(RegistrationPolicy | None)


@dataclass(frozen=True, slots=True)
class RegistrationScope:
    default: RegistrationOrganization
    target: RegistrationOrganization
    invitation: OrgInvitation | None


def effective_signup_policy(scope: RegistrationOrganization) -> RegistrationPolicy:
    policies = (
        settings.registration_policy,
        POLICY.validate_python(scope.tenant.settings.get("registration_policy")),
        OrganizationSettings.model_validate(scope.organization.settings).registration_policy,
    )
    if "closed" in policies:
        return "closed"
    if "invite_only" in policies:
        return "invite_only"
    return "default_org"


def ensure_signup_scope_active(scope: RegistrationOrganization) -> None:
    ensure_tenant_active(scope.tenant)
    if scope.organization.status != "active":
        raise authorization_error("organization_unavailable")


async def prepare_registration(db: AsyncSession, payload: RegisterRequest) -> RegistrationScope:
    if settings.registration_policy == "closed":
        raise authorization_error("registration_closed")
    if settings.registration_policy == "invite_only" and payload.invitation_token is None:
        raise authorization_error("registration_invitation_required")
    digest = None
    if payload.invitation_token is not None:
        digest = hashlib.sha256(payload.invitation_token.get_secret_value().encode()).hexdigest()
    locked = await locked_registration_scope(
        db,
        digest,
        include_default=settings.allow_first_user_as_admin,
    )
    invitation = locked.invitation
    if invitation is not None:
        require_pending_invitation(invitation, str(payload.email))
    target = locked.target
    ensure_signup_scope_active(target)
    policy = effective_signup_policy(target)
    if policy == "closed":
        raise authorization_error("registration_closed")
    if policy == "invite_only" and invitation is None:
        raise authorization_error("registration_invitation_required")
    return RegistrationScope(locked.default, target, invitation)
