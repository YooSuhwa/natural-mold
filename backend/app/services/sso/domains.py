"""``/api/tenants/{tenant_id}/domains`` (plan 9.7, 10.10 "도메인 확인").

Several companies may hold a pending claim on a domain (one each); it becomes
``verified`` — for one company only — when its DNS TXT records contain
``moldy-verify=<token>``, and the competing pending claims are then removed. Other
companies' claims are never revealed (a verified claim elsewhere answers like a
missing TXT record). The last verified domain cannot be
removed while the company requires SSO.
"""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.domains import TXT_PREFIX, new_txt_token, normalize_domain, txt_matches
from app.auth.sso.errors import sso_error
from app.auth.sso.policy import sso_policy
from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.models.organization_identity import TenantDomain
from app.schemas.sso import TenantDomainCreate, TenantDomainList, TenantDomainRead
from app.services.sso.common import SsoMutation, audit, locked_admin
from app.tenancy.admin_context import TenantAdminContext

DomainStatus = Literal["pending", "verified", "disabled"]


def domain_view(row: TenantDomain) -> TenantDomainRead:
    return TenantDomainRead(
        id=row.id,
        domain=row.domain,
        status=cast(DomainStatus, row.status),  # the DB CHECK pins the literal set
        txt_record=TXT_PREFIX + row.txt_token,
        verified_at=row.verified_at,
        created_at=row.created_at,
    )


async def list_domains(db: AsyncSession, tenant_id: UUID) -> TenantDomainList:
    rows = await db.scalars(
        select(TenantDomain)
        .where(TenantDomain.tenant_id == tenant_id)
        .order_by(TenantDomain.domain, TenantDomain.id)
    )
    return TenantDomainList(items=[domain_view(row) for row in rows])


async def _owned(db: AsyncSession, tenant_id: UUID, domain_id: UUID) -> TenantDomain:
    row = await db.get(TenantDomain, domain_id, populate_existing=True)
    if row is None or row.tenant_id != tenant_id:
        raise authorization_error("not_found")
    return row


async def _verified_elsewhere(db: AsyncSession, row: TenantDomain) -> bool:
    found = await db.scalar(
        select(TenantDomain.id).where(
            TenantDomain.domain == row.domain,
            TenantDomain.status == "verified",
            TenantDomain.id != row.id,
        )
    )
    return found is not None


async def add_domain(
    mutation: SsoMutation, context: TenantAdminContext, payload: TenantDomainCreate
) -> TenantDomainRead:
    db = mutation.db
    domain = normalize_domain(payload.domain)
    tenant = await locked_admin(mutation, context)
    # Only this company's own claim conflicts; other companies' claims are never revealed.
    own = select(TenantDomain.id).where(
        TenantDomain.domain == domain, TenantDomain.tenant_id == tenant.id
    )
    if await db.scalar(own):
        raise sso_error("domain_taken")
    row = TenantDomain(tenant_id=tenant.id, domain=domain, txt_token=new_txt_token())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise sso_error("domain_taken") from error
    await db.refresh(row)
    await audit(
        mutation, tenant, audit_actions.TENANT_SSO_DOMAIN_ADD, ("tenant_domain", row.id, domain)
    )
    result = domain_view(row)
    await db.commit()
    return result


async def verify_domain(
    mutation: SsoMutation, context: TenantAdminContext, domain_id: UUID
) -> TenantDomainRead:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await _owned(db, tenant.id, domain_id)
    if row.status != "verified":
        # Another company's verified claim answers exactly like a missing TXT record.
        if await _verified_elsewhere(db, row) or not await txt_matches(row.domain, row.txt_token):
            await db.rollback()
            raise sso_error("domain_not_verified")
        await db.execute(
            delete(TenantDomain).where(
                TenantDomain.domain == row.domain,
                TenantDomain.id != row.id,
                TenantDomain.status != "verified",
            )
        )
        row.status, row.verified_at = "verified", datetime.now(UTC)
        await audit(
            mutation,
            tenant,
            audit_actions.TENANT_SSO_DOMAIN_VERIFY,
            ("tenant_domain", row.id, row.domain),
        )
    try:
        await db.flush()
    except IntegrityError as error:  # lost a race to another company's verification
        await db.rollback()
        raise sso_error("domain_not_verified") from error
    result = domain_view(row)
    await db.commit()
    return result


async def remove_domain(
    mutation: SsoMutation, context: TenantAdminContext, domain_id: UUID
) -> None:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await _owned(db, tenant.id, domain_id)
    if row.status == "verified" and sso_policy(tenant).mode == "required":
        verified = await db.scalar(
            select(func.count(TenantDomain.id)).where(
                TenantDomain.tenant_id == tenant.id, TenantDomain.status == "verified"
            )
        )
        if (verified or 0) <= 1:
            raise sso_error("sso_policy_incomplete")
    await audit(
        mutation,
        tenant,
        audit_actions.TENANT_SSO_DOMAIN_REMOVE,
        ("tenant_domain", row.id, row.domain),
    )
    await db.delete(row)
    await db.commit()
