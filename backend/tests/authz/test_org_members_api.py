"""Membership status, recovery and last-admin protection are organization scoped."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import TenantMember
from app.models.user import User
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_member_add_suspend_remove_and_rejoin_preserves_account(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    user = User(email="org-added-user@example.com", name="Added user")
    db.add(user)
    await db.flush()
    db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=user.id))
    await db.commit()
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    assert original.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    added = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/members",
        json={"user_id": str(user.id)},
        headers=headers,
    )
    assert added.status_code == 201 and added.json()["status"] == "active"
    headers["If-Match"] = added.headers["etag"]
    suspended = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}/members/{user.id}",
        json={"status": "suspended"},
        headers=headers,
    )
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    headers["If-Match"] = suspended.headers["etag"]
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/members/{user.id}", headers=headers
    )
    assert removed.status_code == 204
    assert await db.get(User, user.id) is not None
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    headers["If-Match"] = original.headers["etag"]
    rejoined = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/members",
        json={"user_id": str(user.id)},
        headers=headers,
    )
    assert rejoined.status_code == 200 and rejoined.json()["status"] == "active"
    assert rejoined.json()["removed_at"] is None


async def test_member_roster_hides_email_and_unknown_company_user_cannot_be_added(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    assert original.status_code == 200
    assert original.json()["items"][0]["email"] is None
    await grant_admin(db, org_fixture)
    denied = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/members",
        json={"user_id": str(uuid4())},
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert denied.status_code == 404


async def test_last_active_admin_cannot_be_suspended_or_removed(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    assert original.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    suspended = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}/members/{org_fixture.user.id}",
        json={"status": "suspended"},
        headers=headers,
    )
    assert suspended.status_code == 409 and suspended.json()["error"]["code"] == "last_admin"
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/members/{org_fixture.user.id}", headers=headers
    )
    assert removed.status_code == 409 and removed.json()["error"]["code"] == "last_admin"
