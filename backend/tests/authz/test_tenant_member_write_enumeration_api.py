"""Company member/role writes never reveal a foreign company's lifecycle status."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from app.auth.password import hash_password
from app.models.audit_event import AuditEvent
from app.models.organization import TenantMember, TenantRoleGrant
from app.models.user import User
from tests.authz.tenant_members_fixture import PASSWORD

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]

STATUSES = (("suspended", "tenant_suspended"), ("deletion_scheduled", "tenant_unavailable"))


async def _snapshot(fixture: TenantMembersFixture) -> tuple[object, ...]:
    db = fixture.db
    members = (
        await db.execute(
            select(TenantMember.user_id, TenantMember.status).order_by(TenantMember.user_id)
        )
    ).all()
    roles = (
        await db.execute(
            select(TenantRoleGrant.user_id, TenantRoleGrant.role).order_by(
                TenantRoleGrant.user_id, TenantRoleGrant.role
            )
        )
    ).all()
    audits = await db.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action.like("tenant.%"))
    )
    return tuple(members), tuple(roles), audits


async def _set_status(fixture: TenantMembersFixture, status: str) -> None:
    fixture.tenant.status = status
    await fixture.db.commit()


async def _operator(fixture: TenantMembersFixture) -> User:
    user = User(
        email="member-operator@example.com",
        name="Operator",
        hashed_password=hash_password(PASSWORD),
        is_active=True,
        is_super_user=True,
    )
    fixture.db.add(user)
    await fixture.db.commit()
    return user


async def _hit_all_routes(fixture: TenantMembersFixture, actor: User) -> list[tuple[int, str]]:
    session = await fixture.login(actor)
    version = await fixture.version()
    base = f"/api/tenants/{fixture.tenant.id}"
    client, headers = fixture.client, session.headers(version)
    responses = (
        await client.post(
            f"{base}/members", json={"user_id": str(fixture.inactive.id)}, headers=headers
        ),
        await client.patch(
            f"{base}/members/{fixture.member.id}", json={"status": "suspended"}, headers=headers
        ),
        await client.delete(f"{base}/members/{fixture.member.id}", headers=headers),
        await client.put(
            f"{base}/roles/{fixture.member.id}", json={"roles": ["admin"]}, headers=headers
        ),
    )
    return [(item.status_code, item.json()["error"]["code"]) for item in responses]


@pytest.mark.parametrize("status", [status for status, _ in STATUSES])
@pytest.mark.parametrize("actor_kind", ["foreign", "operator"])
async def test_foreign_actor_sees_uniform_not_found_on_unavailable_company(
    tenant_members_fixture: TenantMembersFixture, status: str, actor_kind: str
) -> None:
    fixture = tenant_members_fixture
    actor = fixture.target if actor_kind == "foreign" else await _operator(fixture)
    await _set_status(fixture, status)
    before = await _snapshot(fixture)

    results = await _hit_all_routes(fixture, actor)

    assert results == [(404, "not_found")] * 4
    assert await _snapshot(fixture) == before


@pytest.mark.parametrize(("status", "code"), STATUSES)
async def test_company_admin_still_sees_lifecycle_denial(
    tenant_members_fixture: TenantMembersFixture, status: str, code: str
) -> None:
    fixture = tenant_members_fixture
    await _set_status(fixture, status)
    before = await _snapshot(fixture)

    results = await _hit_all_routes(fixture, fixture.admin)

    assert results == [(403, code)] * 4
    assert await _snapshot(fixture) == before
