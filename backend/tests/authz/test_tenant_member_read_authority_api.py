"""Member and role directories remain company-admin-only and cursor scoped."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import Response

    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]


def assert_redacted(fixture: TenantMembersFixture, responses: tuple[Response, Response]) -> None:
    forbidden = (
        fixture.admin.email,
        fixture.peer_admin.email,
        fixture.member.email,
        fixture.tenant.secrets_namespace,
    )
    for response in responses:
        assert all(value not in response.text for value in forbidden)


async def test_auditor_cannot_read_member_or_role_directories(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    admin = await fixture.login(fixture.admin)
    changed = await fixture.client.put(
        f"/api/tenants/{fixture.tenant.id}/roles/{fixture.peer_admin.id}",
        json={"roles": ["auditor"]},
        headers=admin.headers(await fixture.version()),
    )
    assert changed.status_code == 200
    await fixture.login(fixture.peer_admin)
    responses = (
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/members"),
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/roles"),
    )
    assert [response.status_code for response in responses] == [403, 403]
    assert all(response.json()["error"]["code"] == "permission_required" for response in responses)
    assert_redacted(fixture, responses)


async def test_plain_member_cannot_read_member_or_role_directories(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    await fixture.login(fixture.member)
    responses = (
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/members"),
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/roles"),
    )
    assert [response.status_code for response in responses] == [403, 403]
    assert_redacted(fixture, responses)


async def test_platform_operator_without_membership_gets_redacted_not_found(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    fixture.target.is_super_user = True
    await fixture.db.commit()
    await fixture.login(fixture.target)
    responses = (
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/members"),
        await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/roles"),
    )
    assert [response.status_code for response in responses] == [404, 404]
    assert all(response.json()["error"]["code"] == "not_found" for response in responses)
    assert_redacted(fixture, responses)


@pytest.mark.parametrize("source", ["members", "roles"])
async def test_member_and_role_cursors_cannot_cross_endpoint_scope(
    tenant_members_fixture: TenantMembersFixture,
    source: str,
) -> None:
    fixture = tenant_members_fixture
    await fixture.login(fixture.admin)
    listed = await fixture.client.get(f"/api/tenants/{fixture.tenant.id}/{source}?limit=1")
    assert listed.status_code == 200
    assert listed.json()["next_cursor"] is not None
    target = "roles" if source == "members" else "members"
    rejected = await fixture.client.get(
        f"/api/tenants/{fixture.tenant.id}/{target}",
        params={"limit": 1, "cursor": listed.json()["next_cursor"]},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_cursor"
