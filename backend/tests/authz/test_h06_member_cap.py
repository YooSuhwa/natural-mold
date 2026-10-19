"""H-06 company ``member_limit`` on add, invitation and acceptance (plan 8.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.services.tenants.versioning import etag
from app.tenancy.limit_capacity import require_invite_capacity, require_member_capacity

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = [
    "tests.authz.tenant_members_fixture",
    "tests.authz.org_context_fixture",
    "tests.authz.tenant_invitation_fixture",
]


async def _cap(db: AsyncSession, tenant: Tenant, cap: int | None) -> None:
    tenant.limits = {**tenant.limits, "member_limit": cap}
    await db.commit()


async def test_adding_a_member_beyond_the_limit_is_429_members(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    await _cap(fixture.db, fixture.tenant, 3)  # admin, peer admin, member
    session = await fixture.login(fixture.admin)
    response = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(await fixture.version()),
    )
    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["code"] == "quota_exceeded"
    assert error["details"] == {"kind": "members", "scope": "tenant", "limit": 3, "used": 3}
    member = await fixture.db.get(TenantMember, (fixture.tenant.id, fixture.target.id))
    assert member is None

    await _cap(fixture.db, fixture.tenant, None)  # no limit: unchanged behaviour
    retry = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(await fixture.version()),
    )
    assert retry.status_code == 201


async def test_inviting_a_new_person_needs_room_but_a_member_does_not(
    db: AsyncSession, tenant_invitation_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    db.add(
        TenantRoleGrant(
            tenant_id=org_fixture.tenant,
            user_id=org_fixture.user.id,
            role="admin",
            granted_by=org_fixture.user.id,
        )
    )
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    members = len(
        (await db.scalars(select(TenantMember).where(TenantMember.tenant_id == tenant.id))).all()
    )
    await _cap(db, tenant, members)
    org_fixture.session.apply(tenant_invitation_client)
    await db.refresh(tenant)
    response = await tenant_invitation_client.post(
        f"/api/tenants/{org_fixture.tenant}/invitations",
        json={"email": "brand-new@example.com", "roles": []},
        headers={**org_fixture.session.headers(), "If-Match": etag(tenant.updated_at)},
    )
    assert response.status_code == 429, response.text
    assert response.json()["error"]["details"]["kind"] == "members"
    await require_invite_capacity(db, tenant.id, org_fixture.user.email.upper())  # a member
    with pytest.raises(AppError) as caught:
        await require_member_capacity(db, tenant.id)
    assert caught.value.status == 429


async def test_open_registration_stops_at_the_member_limit(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    def payload(email: str) -> dict[str, str]:
        return {"email": email, "password": "correct horse", "display_name": "Tester"}

    first = await raw_client.post("/api/auth/register", json=payload("first@test.com"))
    assert first.status_code == 201, first.text
    tenant = (await db.scalars(select(Tenant))).one()
    await _cap(db, tenant, 1)
    raw_client.cookies.clear()
    second = await raw_client.post("/api/auth/register", json=payload("second@test.com"))
    assert second.status_code == 429, second.text
    assert second.json()["error"]["details"]["kind"] == "members"
