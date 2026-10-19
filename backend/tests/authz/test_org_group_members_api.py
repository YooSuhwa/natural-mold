"""Group membership changes require active scoped users and preserve administrator recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantMember
from app.models.organization_group import Group
from app.models.user import User
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_group_membership_add_list_remove_is_scoped_and_versioned(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    user = User(email="new-group-member@example.com", name="New member")
    group = Group(org_id=org_fixture.first, tenant_id=org_fixture.tenant, name="Manual members")
    db.add_all([user, group])
    await db.flush()
    db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=user.id))
    db.add(
        OrganizationMember(org_id=org_fixture.first, tenant_id=org_fixture.tenant, user_id=user.id)
    )
    await db.commit()
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group.id}")
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/groups/{group.id}/members",
        json={"user_id": str(user.id)},
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert created.status_code == 201
    assert created.json()["user_id"] == str(user.id)
    listing = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group.id}/members")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["display_name"] == "New member"
    assert listing.json()["items"][0]["email"] == user.email
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/groups/{group.id}/members/{user.id}",
        headers={**org_fixture.session.headers(), "If-Match": listing.headers["etag"]},
    )
    assert removed.status_code == 204
    assert (
        await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group.id}/members")
    ).json()["items"] == []


async def test_unknown_group_user_is_denied_and_unprivileged_roster_hides_email(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    group = await db.scalar(select(Group).where(Group.org_id == org_fixture.first))
    assert group is not None
    listing = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group.id}/members")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["email"] is None
    await grant_admin(db, org_fixture)
    denied = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/groups/{group.id}/members",
        json={"user_id": str(uuid4())},
        headers={**org_fixture.session.headers(), "If-Match": listing.headers["etag"]},
    )
    assert denied.status_code == 404


async def test_removing_sole_admin_group_member_rolls_back_the_link(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    role = await db.scalar(
        select(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == org_fixture.first,
            OrganizationRoleGrant.principal_type == "group",
        )
    )
    assert role is not None
    role.role = "admin"
    await db.commit()
    listing = await raw_client.get(
        f"/api/orgs/{org_fixture.first}/groups/{role.principal_id}/members"
    )
    assert listing.status_code == 200
    denied = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/groups/{role.principal_id}/members/{org_fixture.user.id}",
        headers={**org_fixture.session.headers(), "If-Match": listing.headers["etag"]},
    )
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "last_admin"
    assert (
        len(
            (
                await raw_client.get(
                    f"/api/orgs/{org_fixture.first}/groups/{role.principal_id}/members"
                )
            ).json()["items"]
        )
        == 1
    )
