"""Self-service signup company: an uninvited account gets its own tenant and organization.

``REGISTRATION_POLICY=personal_tenant`` (``multi_tenant`` only). The company, its default
organization, the owner's administrator grants and the audit trail are written inside the
signup transaction; the platform catalog fanout runs after the commit on a platform session,
like the operator ``catalog-fanout`` backfill, because the signup session is bound to the
identity scope and cannot write company grants under RLS.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.catalog_fanout import organization_fanout, sync_fanout
from app.authz.membership_projection import OrgSubject
from app.authz.membership_sync import queue_created
from app.authz.org_context import OrgContext
from app.authz.reconcile import reconcile_sessions
from app.authz.registration_queries import RegistrationOrganization
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.config import settings
from app.models.organization import Organization, Tenant
from app.models.user import User
from app.services import audit_service
from app.services.registration_membership import DefaultEnrollment, enroll_default_organization
from app.services.tenants.identifiers import DEFAULT_ORGANIZATION_SLUG, tenant_secrets_namespace
from app.tenancy.default_limits import parse_default_limits

logger = logging.getLogger(__name__)

PERSONAL_TENANT_KIND: Final = "personal"
_NAME_MAX_LENGTH: Final = 200


def personal_tenant_limits() -> dict[str, JsonValue]:
    """``TENANT_DEFAULT_LIMITS`` keys that were set; an absent key means unlimited/allowed."""
    return parse_default_limits(settings.tenant_default_limits)


def personal_tenant_name(user: User) -> str:
    owner = (user.display_name or user.name or user.email.split("@", 1)[0]).strip()
    return f"{owner}의 워크스페이스"[:_NAME_MAX_LENGTH]


async def create_personal_tenant(
    db: AsyncSession, user: User, request: Request
) -> RegistrationOrganization:
    now = datetime.now(UTC)
    tenant_id, organization_id = uuid4(), uuid4()
    name = personal_tenant_name(user)
    tenant = Tenant(
        id=tenant_id,
        name=name,
        slug=f"personal-{tenant_id.hex[:24]}",
        status="active",
        limits=personal_tenant_limits(),
        settings={"kind": PERSONAL_TENANT_KIND},
        secrets_namespace=tenant_secrets_namespace(tenant_id),
        data_keys={},
        created_at=now,
        updated_at=now,
    )
    organization = Organization(
        id=organization_id,
        tenant_id=tenant_id,
        name=name,
        slug=DEFAULT_ORGANIZATION_SLUG,
        status="active",
        is_default=True,
        settings={"default_member_role": "member"},
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(tenant)
    await db.flush()
    db.add(organization)
    await db.flush()
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=audit_actions.TENANT_CREATE,
        target_type="tenant",
        target_id=tenant_id,
        target_name_snapshot=name,
        outcome="success",
        request=request,
        metadata={
            "tenant_id": str(tenant_id),
            "org_id": str(organization_id),
            "source": "registration",
            "kind": PERSONAL_TENANT_KIND,
        },
    )
    # 7.2.1 "조직 생성": tenant:T tenant organization:O, before the member tuples.
    await queue_created(db, tenant_id, [OrgSubject(organization_id)])
    scope = RegistrationOrganization(organization, tenant)
    await enroll_default_organization(
        db, DefaultEnrollment(scope, user, bootstrap=False, personal=True), request
    )
    return scope


async def fan_out_personal_catalog(request: Request, user_id: UUID, org_id: UUID) -> None:
    """Best effort after the signup commit; ``cli catalog-fanout --org`` repairs a failure.

    The grants are written to OpenFGA before the response (like operator company creation),
    so the new account's first screen already lists the platform catalog instead of waiting
    for the outbox drain.
    """
    authz = await get_authz(request)
    if authz.mode == "legacy":
        return
    try:
        async with reconcile_sessions()() as db:
            organization = await db.scalar(
                select(Organization).where(Organization.id == org_id).with_for_update()
            )
            if organization is None:
                return
            ctx = OrgContext(org_id=org_id, tenant_id=organization.tenant_id, user_id=user_id)
            change = await sync_fanout(db, ctx, await organization_fanout(db, organization))
            await db.commit()
        await apply_lifecycle(change, authz)
    except Exception:
        logger.exception("personal tenant catalog fanout failed org_id=%s", org_id)
