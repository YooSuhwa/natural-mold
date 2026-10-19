"""Organization metadata is readable by members and governable by scoped admins."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrganizationRoleGrant, TenantRoleGrant
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_member_reads_org_metadata_and_cannot_change_policy(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an active builder/member without organization governance authority.
    response = await raw_client.get(f"/api/orgs/{org_fixture.first}")
    # When: the member reads organization information.
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(org_fixture.first)
    assert body["tenant_id"] == str(org_fixture.tenant)
    assert body["settings"]["default_member_role"] == "member"
    assert "can_manage_policy" not in body["permissions"]
    # Then: policy changes still need explicit governance authority.
    forbidden = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}",
        json={"name": "Changed"},
        headers={**org_fixture.session.headers(), "If-Match": response.headers["etag"]},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_required"


async def test_scoped_admin_updates_policy_and_stale_version_is_rejected(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    db.add(
        OrganizationRoleGrant(
            org_id=org_fixture.first,
            tenant_id=org_fixture.tenant,
            role="admin",
            principal_type="user",
            principal_id=org_fixture.user.id,
        )
    )
    await db.commit()
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}")
    assert original.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    # When: an organization admin updates metadata and one known policy field.
    changed = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}",
        json={"name": "New organization", "settings": {"default_member_role": "builder"}},
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "New organization"
    assert changed.json()["settings"]["default_member_role"] == "builder"
    assert changed.headers["etag"] != original.headers["etag"]
    # Then: a concurrent editor's old version cannot overwrite the newer policy.
    stale = await raw_client.patch(
        f"/api/orgs/{org_fixture.first}",
        json={"name": "Stale overwrite"},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"
    assert (await raw_client.get(f"/api/orgs/{org_fixture.first}")).json()[
        "name"
    ] == "New organization"


@pytest.mark.parametrize("operator", [False, True])
async def test_foreign_and_missing_orgs_are_identical_without_operator_bypass(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    operator: bool,
) -> None:
    await db.execute(
        update(User).where(User.id == org_fixture.user.id).values(is_super_user=operator)
    )
    await db.commit()
    own = await raw_client.get(f"/api/orgs/{org_fixture.first}")
    assert own.status_code == 200
    responses = [await raw_client.get(f"/api/orgs/{key}") for key in (org_fixture.foreign, uuid4())]
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json()


async def test_company_governance_manages_other_org_without_resource_content_access(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: another organization in the member's company, with no org membership.
    organization = Organization(tenant_id=org_fixture.tenant, name="Other", slug="other")
    db.add(organization)
    await db.commit()
    assert (await raw_client.get(f"/api/orgs/{organization.id}")).status_code == 404
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    # When: the company grants governance authority, metadata/policy become manageable.
    response = await raw_client.get(f"/api/orgs/{organization.id}")
    assert response.status_code == 200
    assert set(response.json()["permissions"]) == {"can_manage_members", "can_manage_policy"}
    changed = await raw_client.patch(
        f"/api/orgs/{organization.id}",
        json={"name": "Governed"},
        headers={**org_fixture.session.headers(), "If-Match": response.headers["etag"]},
    )
    assert changed.status_code == 200
    # Then: this metadata response does not carry any resource-content permission.
    assert "can_view" not in changed.json()["permissions"]


async def test_org_policy_change_requires_csrf_before_lookup(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    response = await raw_client.patch(f"/api/orgs/{uuid4()}", json={"name": "Forbidden"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"
