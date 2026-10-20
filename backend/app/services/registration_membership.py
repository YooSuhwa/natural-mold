"""Default enrollment and explicit first-user bootstrap remain inside the signup transaction."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.membership_sync import capture_user
from app.authz.registration_queries import RegistrationOrganization
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.user import User
from app.schemas.org import OrganizationSettings
from app.services import audit_service
from app.services.orgs.versioning import bump_version
from app.services.registration_policy import ensure_signup_scope_active
from app.tenancy.limit_capacity import require_member_capacity


@dataclass(frozen=True, slots=True)
class DefaultEnrollment:
    scope: RegistrationOrganization
    user: User
    bootstrap: bool
    personal: bool = False  # the account's own new company: it administers it


async def enroll_default_organization(
    db: AsyncSession, enrollment: DefaultEnrollment, request: Request
) -> None:
    org, company, user = (
        enrollment.scope.organization,
        enrollment.scope.tenant,
        enrollment.user,
    )
    ensure_signup_scope_active(enrollment.scope)
    administrator = enrollment.bootstrap or enrollment.personal
    if not administrator:
        await require_member_capacity(db, company.id)  # H-06 member_limit
    sync = await capture_user(db, company.id, user.id, org.id)
    db.add(TenantMember(tenant_id=company.id, user_id=user.id))
    db.add(
        OrganizationMember(org_id=org.id, tenant_id=company.id, user_id=user.id, status="active")
    )
    role = (
        "admin"
        if administrator
        else OrganizationSettings.model_validate(org.settings).default_member_role
    )
    events: list[tuple[str, str, UUID, dict[str, JsonValue]]] = [
        (audit_actions.TENANT_MEMBER_ADD, "tenant_member", user.id, {}),
        (audit_actions.ORG_MEMBER_ADD, "organization_member", user.id, {}),
    ]
    if role != "member":
        db.add(
            OrganizationRoleGrant(
                org_id=org.id,
                tenant_id=company.id,
                role=role,
                principal_type="user",
                principal_id=user.id,
                granted_by=user.id,
            )
        )
        events.append(
            (
                audit_actions.ORG_ROLE_GRANT,
                "organization",
                org.id,
                {"role": role, "principal_type": "user"},
            )
        )
    if administrator:
        db.add(
            TenantRoleGrant(tenant_id=company.id, user_id=user.id, role="admin", granted_by=user.id)
        )
        events.append((audit_actions.TENANT_ROLE_GRANT, "tenant", company.id, {"role": "admin"}))
    user.last_active_org_id = org.id
    bump_version(org)
    await sync.queue(db)  # 7.2.1 "구성원 추가": member/role tuples without the nightly reconcile
    for action, target_type, target_id, details in events:
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=user.id,
            actor_email_snapshot=user.email,
            owner_user_id=user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome="success",
            request=request,
            metadata={
                **details,
                "source": "registration",
                "bootstrap": enrollment.bootstrap,
                "personal": enrollment.personal,
                "org_id": str(org.id),
                "tenant_id": str(company.id),
                "principal_id": str(user.id),
                "authz_epoch": org.authz_epoch,
            },
        )
