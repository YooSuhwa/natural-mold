"""Typed default, organization-invite and company-invite signup preparation."""

import hashlib
from dataclasses import dataclass
from typing import Final

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.registration_queries import RegistrationOrganization, locked_registration_scope
from app.config import settings
from app.models.organization import Tenant
from app.models.organization_group import OrgInvitation
from app.models.tenant_invitation import TenantInvitation
from app.schemas.auth import RegisterRequest
from app.schemas.org import OrganizationSettings, RegistrationPolicy
from app.services.orgs.invitation_acceptance import require_pending_invitation
from app.services.tenants.invitation_acceptance import require_pending_tenant_invitation
from app.services.tenants.invitation_queries import locked_registration_invitation
from app.tenancy.context import ensure_tenant_active

POLICY: Final = TypeAdapter(RegistrationPolicy | None)


@dataclass(frozen=True, slots=True)
class RegistrationScope:
    default: RegistrationOrganization
    target: RegistrationOrganization
    invitation: OrgInvitation | None


@dataclass(frozen=True, slots=True)
class DefaultRegistrationScope:
    default: RegistrationOrganization


@dataclass(frozen=True, slots=True)
class TenantInvitationRegistrationScope:
    target: Tenant
    invitation: TenantInvitation
    bootstrap_tenant: Tenant | None


PreparedRegistration = (
    DefaultRegistrationScope | RegistrationScope | TenantInvitationRegistrationScope
)


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


def _require_global_policy(payload: RegisterRequest) -> None:
    if settings.registration_policy == "closed":
        raise authorization_error("registration_closed")
    if settings.registration_policy == "invite_only" and payload.invitation_token is None:
        raise authorization_error("registration_invitation_required")


async def prepare_registration(db: AsyncSession, payload: RegisterRequest) -> PreparedRegistration:
    _require_global_policy(payload)
    token = payload.invitation_token
    if token is not None and token.get_secret_value().startswith("tv1."):
        return await _prepare_tenant_invitation(db, payload)
    digest = None
    if token is not None:
        digest = hashlib.sha256(token.get_secret_value().encode()).hexdigest()
    locked = await locked_registration_scope(
        db,
        digest,
        include_default=settings.allow_first_user_as_admin,
    )
    invitation = locked.invitation
    if invitation is not None:
        require_pending_invitation(invitation, str(payload.email))
    ensure_signup_scope_active(locked.target)
    policy = effective_signup_policy(locked.target)
    if policy == "closed":
        raise authorization_error("registration_closed")
    if policy == "invite_only" and invitation is None:
        raise authorization_error("registration_invitation_required")
    if invitation is None:
        return DefaultRegistrationScope(locked.default)
    return RegistrationScope(locked.default, locked.target, invitation)


async def _prepare_tenant_invitation(
    db: AsyncSession, payload: RegisterRequest
) -> TenantInvitationRegistrationScope:
    token = payload.invitation_token
    if token is None:
        raise authorization_error("tenant_invitation_invalid")
    digest = hashlib.sha256(token.get_secret_value().encode()).hexdigest()
    tenant, invitation, bootstrap = await locked_registration_invitation(
        db, digest, settings.allow_first_user_as_admin
    )
    require_pending_tenant_invitation(invitation, str(payload.email))
    ensure_tenant_active(tenant)
    tenant_policy = POLICY.validate_python(tenant.settings.get("registration_policy"))
    if tenant_policy == "closed":
        raise authorization_error("registration_closed")
    return TenantInvitationRegistrationScope(tenant, invitation, bootstrap)
