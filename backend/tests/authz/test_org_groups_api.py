"""Manual organization groups are scoped, versioned and governed."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrganizationRoleGrant
from app.models.organization_group import Group

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def grant_admin(db: AsyncSession, fixture: OrgFixture) -> None:
    db.add(
        OrganizationRoleGrant(
            org_id=fixture.first,
            tenant_id=fixture.tenant,
            principal_type="user",
            principal_id=fixture.user.id,
            role="admin",
        )
    )
    await db.commit()


async def test_manual_group_crud_has_scoped_cursor_and_version_checks(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}")
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/groups",
        json={"name": "Operators", "description": "Manual"},
        headers=headers,
    )
    assert created.status_code == 201
    group_id = created.json()["id"]
    listing = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups?limit=1")
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1
    assert listing.json()["has_more"]
    cursor = listing.json()["next_cursor"]
    following = await raw_client.get(
        f"/api/orgs/{org_fixture.first}/groups", params={"cursor": cursor, "limit": 1}
    )
    assert following.status_code == 200
    assert following.json()["items"][0]["id"] != listing.json()["items"][0]["id"]
    headers["If-Match"] = listing.headers["etag"]
    changed = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}/groups/{group_id}",
        json={"name": "Renamed"},
        headers=headers,
    )
    assert changed.status_code == 200 and changed.json()["name"] == "Renamed"
    stale = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/groups/{group_id}", headers=headers
    )
    assert stale.status_code == 409
    headers["If-Match"] = changed.headers["etag"]
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/groups/{group_id}", headers=headers
    )
    assert removed.status_code == 204
    assert (
        await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group_id}")
    ).status_code == 404


async def test_builder_can_list_groups_but_not_create_them(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    listing = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["name"] == "Builders"
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/groups",
        json={"name": "Forbidden"},
        headers={**org_fixture.session.headers(), "If-Match": listing.headers["etag"]},
    )
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "permission_required"


async def test_other_org_group_and_missing_group_have_identical_errors(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    foreign_org = await db.get(Organization, org_fixture.second)
    assert foreign_org is not None
    other = Group(org_id=foreign_org.id, tenant_id=foreign_org.tenant_id, name="Other")
    db.add(other)
    await db.commit()
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups")
    assert original.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    for method in ("get", "delete"):
        results = [
            await raw_client.request(
                method, f"/api/orgs/{org_fixture.first}/groups/{key}", headers=headers
            )
            for key in (other.id, uuid4())
        ]
        assert [result.status_code for result in results] == [404, 404]
        assert results[0].json() == results[1].json()


async def test_idp_group_is_read_only_for_manual_management(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    managed = Group(
        org_id=org_fixture.first, tenant_id=org_fixture.tenant, name="Directory", source="idp"
    )
    db.add(managed)
    await db.commit()
    read = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{managed.id}")
    assert read.status_code == 200 and read.json()["source"] == "idp"
    changed = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}/groups/{managed.id}",
        json={"name": "Manual overwrite"},
        headers={**org_fixture.session.headers(), "If-Match": read.headers["etag"]},
    )
    assert changed.status_code == 403
    assert changed.json()["error"]["code"] == "policy_locked"


async def test_group_delete_cannot_remove_last_effective_org_administrator(
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
    group_id = role.principal_id
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group_id}")
    assert original.status_code == 200
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/groups/{group_id}",
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert removed.status_code == 409
    assert removed.json()["error"]["code"] == "last_admin"
    assert (
        await raw_client.get(f"/api/orgs/{org_fixture.first}/groups/{group_id}")
    ).status_code == 200
