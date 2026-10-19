"""E-01: grant list, access list and revocation (plan 9.7, 10.2, 8.4, 8.6)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import ResourceGrant
from tests.authz.d04_support import AuthzSwitch, Unavailable, allow, authz
from tests.authz.e01_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_agent,
    add_group,
    add_tool,
    audit_actions,
    base,
    give_cap,
    grants,
    make_admin,
    outbox_count,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _share(client: AsyncClient, kind: str, rid: object, ptype: str, pid: object, level: str):
    body = {"principal": {"type": ptype, "id": str(pid)}, "level": level}
    response = await client.post(f"{base(kind, rid)}/grants", json=body)
    assert response.status_code == 201, response.text
    return response.json()["grant_id"]


async def test_grant_list_shows_active_share_grants(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db)
    grant_id = await _share(client, "tool", tool.id, "user", OTHER_USER_ID, "editor")
    response = await client.get(f"{base('tool', tool.id)}/grants")
    assert response.status_code == 200
    [item] = response.json()["items"]
    assert (item["grant_id"], item["level"]) == (grant_id, "editor")


async def test_access_list_includes_owner_grants_and_governance(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    group = await add_group(db, members=(OTHER_USER_ID, TEST_USER_ID))
    await give_cap(db, "cap_share_org_wide")
    await make_admin(db, OTHER_USER_ID)
    await _share(client, "tool", tool.id, "user", OTHER_USER_ID, "viewer")
    await _share(client, "tool", tool.id, "group", group, "consumer")
    await _share(client, "tool", tool.id, "organization", TEST_ORG_ID, "consumer")
    response = await client.get(f"{base('tool', tool.id)}/access")
    assert response.status_code == 200
    body = response.json()
    rows = [
        (i["source"], i["principal"]["type"], i["level"], i["revocable"]) for i in body["items"]
    ]
    assert rows == [
        ("owner", "user", "owner", False),
        ("direct", "user", "viewer", True),
        ("group", "group", "consumer", True),
        ("organization", "organization", "consumer", True),
        ("governance", "user", "governor", False),
    ]
    assert body["items"][2]["principal"]["member_count"] == 2
    assert body["items"][0]["principal"]["display_name"] is not None
    assert body["my_permissions"] == ["can_share", "can_revoke", "can_govern"]


async def test_revoke_marks_revoking_queues_deletes_and_audits(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    grant_id = await _share(client, "tool", tool.id, "user", OTHER_USER_ID, "viewer")
    queued = await outbox_count()
    response = await client.delete(f"/api/grants/{grant_id}")
    assert response.status_code == 204
    [grant] = await grants(tool.id)
    assert grant.state == "revoking"
    assert await outbox_count() > queued
    assert await audit_actions(tool.id) == ["grant.create", "grant.revoke"]
    assert (await client.delete(f"/api/grants/{grant_id}")).status_code == 404


async def test_delegation_and_foreign_grants_are_not_revocable_here(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool, agent = await add_tool(db), await add_agent(db)
    foreign_tool = await add_tool(db, org=OTHER_ORG_ID)
    delegation = ResourceGrant(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="tool",
        resource_id=tool.id,
        relation="consumer",
        principal_type="agent",
        principal_id=agent.id,
        grant_kind="delegation",
    )
    foreign = ResourceGrant(
        org_id=OTHER_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="tool",
        resource_id=foreign_tool.id,
        relation="viewer",
        principal_type="organization",
        principal_id=OTHER_ORG_ID,
    )
    db.add_all([delegation, foreign])
    await db.commit()
    for grant in (delegation, foreign):
        assert (await client.delete(f"/api/grants/{grant.id}")).status_code == 404


async def test_legacy_non_owner_cannot_see_or_share(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    path = base("tool", tool.id)
    body = {"principal": {"type": "user", "id": str(TEST_USER_ID)}, "level": "viewer"}
    assert (await client.get(f"{path}/access")).status_code == 404
    assert (await client.post(f"{path}/grants", json=body)).status_code == 404


async def test_enforced_viewer_gets_403_and_stranger_404(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    path = base("tool", tool.id)
    body = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "viewer"}
    authz.use()
    assert (await client.get(f"{path}/access")).status_code == 404
    authz.use(allow(("can_use", obj), ("can_view", obj)))
    denied = await client.post(f"{path}/grants", json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_required"
    assert (await client.get(f"{path}/access")).status_code == 403


async def test_enforced_manager_shares_a_resource_they_do_not_own(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    obj = f"agent:{agent.id}"
    authz.use(allow(("can_run", obj), ("can_share", obj), ("can_revoke", obj)))
    group = await add_group(db, members=(OTHER_USER_ID,))
    grant_id = await _share(client, "agent", agent.id, "group", group, "consumer")
    access = (await client.get(f"{base('agent', agent.id)}/access")).json()
    assert access["my_permissions"] == ["can_share", "can_revoke"]
    assert (await client.delete(f"/api/grants/{grant_id}")).status_code == 204


async def test_enforced_revoke_without_can_revoke_is_404(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db)
    grant_id = await _share(client, "tool", tool.id, "user", OTHER_USER_ID, "viewer")
    authz.use(allow(("can_use", f"tool:{tool.id}")))
    assert (await client.delete(f"/api/grants/{grant_id}")).status_code == 404


async def test_enforced_openfga_outage_is_503(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db)
    authz.use(client=Unavailable())
    response = await client.get(f"{base('tool', tool.id)}/access")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "authz_unavailable"
