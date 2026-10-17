"""Email-first routing (plan 10.10 "로그인"): a verified domain → its company's IdP.

Only ``verified`` domains of an ``active`` company route to SSO; anything else keeps
the password form. The answer is the same for unknown and unverified domains.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.domains import email_domain
from app.auth.sso.errors import sso_error
from app.auth.sso.policy import sso_policy
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection, TenantDomain
from app.schemas.sso import SsoDiscoverResponse, SsoLoginOption
from app.services.sso.common import public_url


async def _company_of(db: AsyncSession, email: str) -> Tenant | None:
    domain = email_domain(email)
    if domain is None:
        return None
    return await db.scalar(
        select(Tenant)
        .join(TenantDomain, TenantDomain.tenant_id == Tenant.id)
        .where(
            TenantDomain.domain == domain,
            TenantDomain.status == "verified",
            Tenant.status == "active",
        )
    )


async def _active_connections(db: AsyncSession, tenant_id: UUID) -> list[SsoConnection]:
    rows = await db.scalars(
        select(SsoConnection)
        .where(SsoConnection.tenant_id == tenant_id, SsoConnection.status == "active")
        .order_by(SsoConnection.created_at, SsoConnection.id)
    )
    return list(rows)


async def refuse_sso_domain_signup(db: AsyncSession, email: str) -> None:
    """Password self-registration is refused for a company domain with an active IdP.

    Read-only and without autoflush: registration checks run before anything pending
    in the session may be written (``test_tenant_invitation_denial_api``).
    """
    with db.no_autoflush:
        tenant = await _company_of(db, email)
        blocked = tenant is not None and bool(await _active_connections(db, tenant.id))
    if blocked:
        raise sso_error("sso_signup_blocked")


async def discover_login(
    db: AsyncSession, request: Request, email: str, login_csrf: str
) -> SsoDiscoverResponse:
    password = SsoDiscoverResponse(
        method="password", sso_required=False, connections=[], login_csrf=login_csrf
    )
    tenant = await _company_of(db, email)
    if tenant is None:
        return password
    rows = await _active_connections(db, tenant.id)
    options = [
        SsoLoginOption(
            id=row.id,
            protocol="ldap" if row.protocol == "ldap" else "oidc",
            name=tenant.name,
            start_url=public_url(request, "sso_start", row.id) if row.protocol == "oidc" else None,
        )
        for row in rows
    ]
    if not options:
        return password
    return SsoDiscoverResponse(
        method="sso",
        sso_required=sso_policy(tenant).mode == "required",
        connections=options,
        login_csrf=login_csrf,
    )
