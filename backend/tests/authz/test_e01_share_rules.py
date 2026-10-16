"""E-01: the 10.2 sharing rules on ``POST /api/resources/{type}/{id}/grants`` (plan 9.7)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import ResourceGrant
from tests.authz.d04_support import AuthzSwitch, allow, authz
from tests.authz.e01_support import (
    FOREIGN_USER_ID,
    OTHER_ORG_ID,
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_agent,
    add_credential,
    add_group,
    add_tool,
    audit_actions,
    base,
    give_cap,
    grants,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _user(user_id: object, level: str = "viewer", **extra: object) -> dict:
    return {"principal": {"type": "user", "id": str(user_id)}, "level": level, **extra}


async def test_owner_shares_with_a_member_and_the_grant_is_audited_and_queued(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    response = await client.post(f"{base('tool', tool.id)}/grants", json=_user(OTHER_USER_ID))
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["level"], body["principal"]["id"]) == ("viewer", str(OTHER_USER_ID))
    assert body["granted_by"] == str(TEST_USER_ID)
    [grant] = await grants(tool.id)
    assert (grant.state, grant.grant_kind, grant.relation) == ("active", "share", "viewer")
    assert await audit_actions(tool.id) == ["grant.create"]


async def test_same_principal_and_level_returns_the_existing_grant_with_200(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    first = await client.post(f"{base('tool', tool.id)}/grants", json=_user(OTHER_USER_ID))
    again = await client.post(f"{base('tool', tool.id)}/grants", json=_user(OTHER_USER_ID))
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["grant_id"] == first.json()["grant_id"]
    assert len(await grants(tool.id)) == 1
    assert await audit_actions(tool.id) == ["grant.create"]


async def test_level_change_revokes_the_old_grant_and_creates_a_new_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    path = f"{base('tool', tool.id)}/grants"
    old = (await client.post(path, json=_user(OTHER_USER_ID, "editor"))).json()
    new = await client.post(path, json=_user(OTHER_USER_ID, "viewer"))
    assert new.status_code == 201
    assert new.json()["grant_id"] != old["grant_id"]
    states = {(g.relation, g.state) for g in await grants(tool.id)}
    assert states == {("editor", "revoking"), ("viewer", "active")}
    assert sorted(await audit_actions(tool.id)) == ["grant.create", "grant.create", "grant.revoke"]


@pytest.mark.parametrize("level", ["owner", "installer", "blocked"])
async def test_levels_above_manager_or_outside_10_1_are_rejected(
    client: AsyncClient, db: AsyncSession, level: str
) -> None:
    tool = await add_tool(db)
    response = await client.post(
        f"{base('tool', tool.id)}/grants", json=_user(OTHER_USER_ID, level)
    )
    assert response.status_code == 422
    assert await grants(tool.id) == []


@pytest.mark.parametrize(("level", "status"), [("viewer", 422), ("editor", 422), ("manager", 201)])
async def test_credentials_only_take_use_and_manage_levels(
    client: AsyncClient, db: AsyncSession, level: str, status: int
) -> None:
    credential = await add_credential(db)
    path = f"{base('credential', credential.id)}/grants"
    response = await client.post(path, json=_user(OTHER_USER_ID, level))
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["error"]["details"] == {"reason": "level"}


async def test_the_owner_is_never_a_grant_principal(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db)
    response = await client.post(f"{base('tool', tool.id)}/grants", json=_user(TEST_USER_ID))
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "owner"}


async def test_expiry_is_for_agent_run_shares_only(client: AsyncClient, db: AsyncSession) -> None:
    expires = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    tool, agent = await add_tool(db), await add_agent(db)
    on_tool = await client.post(
        f"{base('tool', tool.id)}/grants", json=_user(OTHER_USER_ID, "consumer", expires_at=expires)
    )
    agent_view = await client.post(
        f"{base('agent', agent.id)}/grants", json=_user(OTHER_USER_ID, expires_at=expires)
    )
    agent_run = await client.post(
        f"{base('agent', agent.id)}/grants",
        json=_user(OTHER_USER_ID, "consumer", expires_at=expires),
    )
    assert (on_tool.status_code, agent_view.status_code, agent_run.status_code) == (422, 422, 201)
    assert agent_run.json()["expires_at"] is not None


async def test_a_user_of_another_organization_is_not_a_share_target(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    response = await client.post(f"{base('tool', tool.id)}/grants", json=_user(FOREIGN_USER_ID))
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"reason": "principal"}


async def test_groups_and_organizations_of_another_organization_are_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    foreign_group = await add_group(db, org=OTHER_ORG_ID)
    path = f"{base('tool', tool.id)}/grants"
    group = {"principal": {"type": "group", "id": str(foreign_group)}, "level": "viewer"}
    org = {"principal": {"type": "organization", "id": str(OTHER_ORG_ID)}, "level": "viewer"}
    await give_cap(db, "cap_share_org_wide")
    assert (await client.post(path, json=group)).status_code == 422
    assert (await client.post(path, json=org)).status_code == 422
    assert await grants(tool.id) == []


async def test_agents_are_not_share_principals(client: AsyncClient, db: AsyncSession) -> None:
    tool, agent = await add_tool(db), await add_agent(db)
    body = {"principal": {"type": "agent", "id": str(agent.id)}, "level": "consumer"}
    assert (await client.post(f"{base('tool', tool.id)}/grants", json=body)).status_code == 422


async def test_resources_of_another_organization_are_hidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    foreign = await add_tool(db, org=OTHER_ORG_ID)
    response = await client.post(f"{base('tool', foreign.id)}/grants", json=_user(OTHER_USER_ID))
    assert response.status_code == 404


async def test_company_credentials_are_not_shared_through_this_api(
    client: AsyncClient, db: AsyncSession
) -> None:
    credential = await add_credential(db, scope="tenant")
    path = f"{base('credential', credential.id)}/grants"
    assert (await client.post(path, json=_user(OTHER_USER_ID, "consumer"))).status_code == 404


async def test_organization_wide_share_needs_cap_share_org_wide(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    path = f"{base('tool', tool.id)}/grants"
    body = {"principal": {"type": "organization", "id": str(TEST_ORG_ID)}, "level": "consumer"}
    denied = await client.post(path, json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "capability_required"
    await give_cap(db, "cap_share_org_wide")
    assert (await client.post(path, json=body)).status_code == 201


async def test_enforced_org_wide_share_asks_the_capability_of_openfga(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db)
    obj = f"tool:{tool.id}"
    body = {"principal": {"type": "organization", "id": str(TEST_ORG_ID)}, "level": "viewer"}
    authz.use(allow(("can_use", obj), ("can_share", obj)))
    denied = await client.post(f"{base('tool', tool.id)}/grants", json=body)
    assert denied.json()["error"]["code"] == "capability_required"
    authz.use(
        allow(
            ("can_use", obj),
            ("can_share", obj),
            ("cap_share_org_wide", f"organization:{TEST_ORG_ID}"),
        )
    )
    assert (await client.post(f"{base('tool', tool.id)}/grants", json=body)).status_code == 201


async def test_sharing_an_agent_with_delegated_accounts_needs_confirmation(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, credential = await add_agent(db), await add_credential(db)
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="credential",
            resource_id=credential.id,
            relation="consumer",
            principal_type="agent",
            principal_id=agent.id,
            grant_kind="delegation",
        )
    )
    await db.commit()
    path = f"{base('agent', agent.id)}/grants"
    first = await client.post(path, json=_user(OTHER_USER_ID, "consumer"))
    assert first.status_code == 409
    error = first.json()["error"]
    assert error["code"] == "confirm_required"
    assert error["details"]["delegations"] == [
        {"resource_type": "credential", "resource_id": str(credential.id), "name": "Slack"}
    ]
    confirmed = await client.post(
        path, json=_user(OTHER_USER_ID, "consumer", confirm_delegation=True)
    )
    assert confirmed.status_code == 201


async def test_group_share_is_stored_for_the_group(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db)
    group = await add_group(db, members=(OTHER_USER_ID,))
    body = {"principal": {"type": "group", "id": str(group)}, "level": "editor"}
    assert (await client.post(f"{base('tool', tool.id)}/grants", json=body)).status_code == 201
    [grant] = await grants(tool.id)
    assert (grant.principal_type, grant.principal_id) == ("group", group)
