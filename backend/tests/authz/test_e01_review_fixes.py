"""E-01 review fixes: credential owner-only sharing, self grants/revokes, transfer blocks,
owner re-check and governance audit (plan 10.2, 10.4, 10.7)."""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.share_targets import SHARE_TYPES, recheck_owner
from app.exceptions import AppError
from app.models.agent_trigger import AgentTrigger
from app.models.authz import ResourceGrant
from tests.authz.d04_support import AuthzSwitch, allow, authz
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_agent,
    add_credential,
    add_group,
    add_tool,
    audit_actions,
    audit_event,
    base,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _to(user_id: object, level: str = "consumer") -> dict:
    return {"principal": {"type": "user", "id": str(user_id)}, "level": level}


async def _direct(db: AsyncSession, kind: str, rid: object, user: object, level: str) -> UUID:
    grant = ResourceGrant(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type=kind,
        resource_id=rid,
        relation=level,
        principal_type="user",
        principal_id=user,
    )
    db.add(grant)
    await db.commit()
    return grant.id


async def test_non_owner_with_can_share_cannot_share_a_credential(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    credential = await add_credential(db, owner=OTHER_USER_ID)
    obj = f"credential:{credential.id}"
    path = f"{base('credential', credential.id)}/grants"
    authz.use(allow(("can_use", obj), ("can_share", obj)))  # manager grantee / company admin
    visible = await client.post(path, json=_to(OTHER_USER_ID))
    assert visible.status_code == 403
    assert visible.json()["error"]["code"] == "permission_required"
    authz.use(allow(("can_share", obj)))  # company admin who cannot use it: hidden
    assert (await client.post(path, json=_to(OTHER_USER_ID))).status_code == 404


async def test_non_owner_cannot_grant_themselves_more(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    authz.use(allow(("can_use", obj), ("can_share", obj)))  # manager through a group
    path = f"{base('tool', tool.id)}/grants"
    raised = await client.post(path, json=_to(TEST_USER_ID, "manager"))
    assert raised.status_code == 422
    assert raised.json()["error"]["details"] == {"reason": "self_grant"}
    await _direct(db, "tool", tool.id, TEST_USER_ID, "manager")
    lowered = await client.post(path, json=_to(TEST_USER_ID, "viewer"))
    assert lowered.status_code == 201, lowered.text


async def test_grantee_revokes_their_own_grant_without_can_revoke(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    grant_id = await _direct(db, "tool", tool.id, TEST_USER_ID, "viewer")
    authz.use()  # no permission at all
    assert (await client.delete(f"/api/grants/{grant_id}")).status_code == 204
    event = await audit_event(tool.id, "grant.revoke")
    assert event.event_metadata is not None
    assert event.event_metadata["self_revoke"] is True


async def test_governor_revocation_is_audited_as_governance(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    group = await add_group(db)
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="tool",
            resource_id=tool.id,
            relation="viewer",
            principal_type="group",
            principal_id=group,
        )
    )
    await db.commit()
    [grant] = await _grants(db, tool.id)
    obj = f"tool:{tool.id}"
    authz.use(allow(("can_revoke", obj)))
    assert (await client.delete(f"/api/grants/{grant}")).status_code == 204
    event = await audit_event(tool.id, "grant.revoke")
    assert event.event_metadata is not None
    assert (event.event_metadata["governance"], event.event_metadata["self_revoke"]) == (
        True,
        False,
    )


async def _grants(db: AsyncSession, rid: object) -> list[UUID]:
    return list(await db.scalars(select(ResourceGrant.id).where(ResourceGrant.resource_id == rid)))


async def test_agent_with_an_active_trigger_is_not_transferred(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    trigger = AgentTrigger(
        agent_id=agent.id,
        user_id=TEST_USER_ID,
        trigger_type="interval",
        schedule_config={"minutes": 5},
        input_message="hi",
    )
    trigger.org_id, trigger.tenant_id = TEST_ORG_ID, TEST_TENANT_ID
    db.add(trigger)
    await db.commit()
    body = {"new_owner_id": str(OTHER_USER_ID)}
    response = await client.post(f"{base('agent', agent.id)}/transfer", json=body)
    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"]) == (
        "transfer_blocked",
        {"triggers": [str(trigger.id)]},
    )
    assert "ownership.transfer" not in await audit_actions(agent.id)


async def test_tool_on_the_previous_owners_credential_is_not_transferred(
    client: AsyncClient, db: AsyncSession
) -> None:
    credential = await add_credential(db)
    tool = await add_tool(db)
    tool.credential_id = credential.id
    await db.commit()
    body = {"new_owner_id": str(OTHER_USER_ID)}
    response = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"credentials": [str(credential.id)]}


@pytest.mark.parametrize(
    ("definition", "status"),
    [("google_workspace_oauth2", 409), ("srt_account", 409), ("http_bearer", 200)],
)
async def test_personal_account_credentials_are_never_transferred(
    client: AsyncClient, db: AsyncSession, definition: str, status: int
) -> None:
    credential = await add_credential(db, definition_key=definition)
    body = {"new_owner_id": str(OTHER_USER_ID)}
    response = await client.post(f"{base('credential', credential.id)}/transfer", json=body)
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["error"]["details"] == {"personal_account": definition}


async def test_denied_transfer_writes_no_audit(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    authz.use(allow(("can_use", obj), ("can_govern", obj)))
    body = {"new_owner_id": str(TEST_USER_ID), "reason": "x"}
    assert (await client.post(f"{base('tool', tool.id)}/transfer", json=body)).status_code == 422
    assert await audit_actions(tool.id) == []


async def test_owner_recheck_refuses_a_moved_owner(db: AsyncSession) -> None:
    tool = await add_tool(db)
    await recheck_owner(db, SHARE_TYPES["tool"], tool.id, TEST_USER_ID)
    with pytest.raises(AppError) as error:
        await recheck_owner(db, SHARE_TYPES["tool"], tool.id, OTHER_USER_ID)
    assert error.value.code == "stale_version"
