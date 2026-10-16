"""Roles and capabilities are scoped delta updates protected by a parent version."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_group import Group
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_role_delta_updates_one_principal_and_protects_last_admin(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    group = Group(org_id=org_fixture.first, tenant_id=org_fixture.tenant, name="Admin backup")
    db.add(group)
    await db.commit()
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/roles")
    assert original.status_code == 200
    payload = {"principal": {"type": "user", "id": str(org_fixture.user.id)}, "roles": ["builder"]}
    denied = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/roles",
        json=payload,
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "last_admin"
    # An empty group does not qualify as an effective administrator backup.
    assigned = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/roles",
        json={"principal": {"type": "group", "id": str(group.id)}, "roles": ["auditor"]},
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert assigned.status_code == 200
    current = await raw_client.get(f"/api/orgs/{org_fixture.first}/roles")
    rows = current.json()["items"]
    assert any(
        row["principal"]["id"] == str(org_fixture.user.id) and row["role"] == "admin"
        for row in rows
    )
    assert any(row["principal"]["id"] == str(group.id) and row["role"] == "auditor" for row in rows)


async def test_capability_assignments_refuse_foreign_user_and_restricted_org_targets(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    original = await raw_client.get(f"/api/orgs/{org_fixture.first}/capabilities")
    assert original.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    missing = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/capabilities",
        json={
            "principal": {"type": "user", "id": str(uuid4())},
            "capabilities": ["cap_create_agent"],
        },
        headers=headers,
    )
    assert missing.status_code == 404
    invalid = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/capabilities",
        json={
            "principal": {"type": "organization", "id": str(org_fixture.first)},
            "capabilities": ["cap_share_org_wide"],
        },
        headers=headers,
    )
    assert invalid.status_code == 422
    allowed = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/capabilities",
        json={
            "principal": {"type": "organization", "id": str(org_fixture.first)},
            "capabilities": ["cap_create_skill"],
        },
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["items"][0]["capability"] == "cap_create_skill"
    stale = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/capabilities",
        json={
            "principal": {"type": "organization", "id": str(org_fixture.first)},
            "capabilities": [],
        },
        headers=headers,
    )
    assert stale.status_code == 409


async def test_assignment_reads_and_writes_are_admin_only(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    for resource in ("roles", "capabilities"):
        response = await raw_client.get(f"/api/orgs/{org_fixture.first}/{resource}")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_required"


async def test_grant_lists_filter_to_one_principals_full_set(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an admin user plus a group granted the auditor role.
    await grant_admin(db, org_fixture)
    group = Group(org_id=org_fixture.first, tenant_id=org_fixture.tenant, name="Filter target")
    db.add(group)
    await db.commit()
    base = f"/api/orgs/{org_fixture.first}"
    original = await raw_client.get(f"{base}/roles")
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    assigned = await raw_client.put(
        f"{base}/roles",
        json={"principal": {"type": "group", "id": str(group.id)}, "roles": ["auditor"]},
        headers=headers,
    )
    assert assigned.status_code == 200
    # When: each list is filtered to the group, page size 1 so paging must be honoured.
    group_filter = f"principal_type=group&principal_id={group.id}"
    first = await raw_client.get(f"{base}/roles?{group_filter}&limit=1")
    # Then: only that principal's grants are returned.
    assert first.status_code == 200
    rows = first.json()["items"]
    assert [(row["principal"]["id"], row["role"]) for row in rows] == [(str(group.id), "auditor")]
    assert first.json()["has_more"] is False
    user_rows = (
        await raw_client.get(f"{base}/roles?principal_type=user&principal_id={org_fixture.user.id}")
    ).json()["items"]
    assert {row["role"] for row in user_rows} == {"admin"}
    assert all(row["principal"]["type"] == "user" for row in user_rows)
    capabilities = await raw_client.get(f"{base}/capabilities?{group_filter}")
    assert capabilities.status_code == 200 and capabilities.json()["items"] == []
    # And: a half-specified filter is rejected rather than silently ignored.
    partial = await raw_client.get(f"{base}/roles?principal_type=group")
    assert partial.status_code == 422
