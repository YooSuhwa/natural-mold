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
