"""Company administrator + sibling organization for a seeded execution graph (I23).

Shared by the ordinary app-local HTTP probe and the PostgreSQL-only native
modules: the administrator is a real ``User`` with an active ``TenantMember``
and an ``admin`` ``TenantRoleGrant``; its access/CSRF tokens are minted
directly so archive/restore go through the real authentication, CSRF and
company-authority dependencies of the app-local lifecycle router.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token, create_csrf_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.organization import Organization, TenantMember, TenantRoleGrant
from app.models.user import User
from app.services.orgs.versioning import etag

SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True, slots=True)
class CompanyAdmin:
    user_id: UUID
    tenant_id: UUID
    org_id: UUID
    sibling_id: UUID
    access: str
    csrf: str

    def apply(self, client: AsyncClient) -> None:
        client.cookies.clear()
        client.cookies.set(settings.cookie_name_access, self.access)
        client.cookies.set(settings.cookie_name_csrf, self.csrf)


async def seed_company_admin(db: AsyncSession, context: OrgContext) -> CompanyAdmin:
    """Add an active company admin and a sibling org so archive is not the last org."""

    key = uuid4().hex
    admin = User(email=f"task10-admin-{key}@example.com", name="Task10 admin", is_active=True)
    sibling = Organization(tenant_id=context.tenant_id, name="Sibling", slug=f"sib-{key}")
    db.add_all((admin, sibling))
    await db.flush()
    db.add_all(
        (
            TenantMember(tenant_id=context.tenant_id, user_id=admin.id),
            TenantRoleGrant(tenant_id=context.tenant_id, user_id=admin.id, role="admin"),
        )
    )
    await db.commit()
    return CompanyAdmin(
        admin.id,
        context.tenant_id,
        context.org_id,
        sibling.id,
        create_access_token(admin.id),
        create_csrf_token(admin.id),
    )


async def organization_row(sessions: SessionFactory, org_id: UUID) -> Organization:
    async with sessions() as db:
        row = await db.get(Organization, org_id)
        assert row is not None
        return row


async def lifecycle(
    client: AsyncClient,
    sessions: SessionFactory,
    admin: CompanyAdmin,
    action: str,
    *,
    version: str | None = None,
) -> Response:
    """POST archive/restore for the admin's organization with the current ETag."""

    org = await organization_row(sessions, admin.org_id)
    admin.apply(client)
    return await client.post(
        f"/api/tenants/{admin.tenant_id}/organizations/{admin.org_id}/{action}",
        json={"confirm_name": org.name} if action == "archive" else None,
        headers={"X-CSRF-Token": admin.csrf, "If-Match": version or etag(org.updated_at)},
    )
