"""E-01: ownership transfer (plan 9.7, 10.2 "소유는 이전으로만", 10.7, S17)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import AuthzRevocation
from app.models.tool import Tool
from tests.authz.d04_support import AuthzSwitch, allow, authz
from tests.authz.e01_support import (
    FOREIGN_USER_ID,
    OTHER_USER_ID,
    TEST_USER_ID,
    THIRD_USER_ID,
    add_agent,
    add_member,
    add_skill,
    add_tool,
    audit_event,
    base,
    grants,
    seed,
)
from tests.conftest import TestSession

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _owner(tool_id: object) -> object:
    async with TestSession() as db:
        tool = await db.get(Tool, tool_id)
        assert tool is not None
        return tool.user_id


async def test_owner_transfers_and_may_keep_a_manager_grant(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    body = {"new_owner_id": str(OTHER_USER_ID), "keep_manager": True}
    response = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["owner_id"] == str(OTHER_USER_ID)
    assert result["previous_owner_id"] == str(TEST_USER_ID)
    assert await _owner(tool.id) == OTHER_USER_ID
    [kept] = await grants(tool.id, state="active")
    assert (kept.principal_id, kept.relation, str(kept.id)) == (
        TEST_USER_ID,
        "manager",
        result["manager_grant_id"],
    )
    event = await audit_event(tool.id, "ownership.transfer")
    assert event.event_metadata is not None
    assert event.event_metadata["new_owner_id"] == str(OTHER_USER_ID)
    assert event.event_metadata["governance"] is False


async def test_transfer_revokes_the_new_owners_redundant_direct_grant(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    share = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "editor"}
    await client.post(f"{base('tool', tool.id)}/grants", json=share)
    body = {"new_owner_id": str(OTHER_USER_ID)}
    assert (await client.post(f"{base('tool', tool.id)}/transfer", json=body)).status_code == 200
    assert await grants(tool.id, state="active") == []


async def test_transfer_to_the_current_owner_is_unchanged(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    body = {"new_owner_id": str(TEST_USER_ID)}
    response = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert response.status_code == 200
    assert response.json()["previous_owner_id"] == str(TEST_USER_ID)


async def test_transfer_needs_a_member_of_the_organization(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    body = {"new_owner_id": str(FOREIGN_USER_ID)}
    response = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert response.status_code == 422
    assert await _owner(tool.id) == TEST_USER_ID


async def test_skill_name_clash_with_the_new_owner_is_409(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await add_skill(db, owner=TEST_USER_ID, slug="recruit")
    await add_skill(db, owner=OTHER_USER_ID, slug="recruit")
    body = {"new_owner_id": str(OTHER_USER_ID)}
    response = await client.post(f"{base('skill', mine.id)}/transfer", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transfer_name_conflict"


async def test_legacy_non_owner_cannot_transfer(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    body = {"new_owner_id": str(TEST_USER_ID), "reason": "leaving"}
    assert (await client.post(f"{base('tool', tool.id)}/transfer", json=body)).status_code == 404


async def test_governor_may_not_take_ownership_and_must_give_a_reason(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    await add_member(db, THIRD_USER_ID)
    obj = f"agent:{agent.id}"
    authz.use(allow(("can_run", obj), ("can_govern", obj)))
    path = f"{base('agent', agent.id)}/transfer"
    to_self = await client.post(path, json={"new_owner_id": str(TEST_USER_ID), "reason": "x"})
    assert to_self.status_code == 422
    assert to_self.json()["error"]["details"] == {"reason": "self_transfer"}
    no_reason = await client.post(path, json={"new_owner_id": str(THIRD_USER_ID)})
    assert no_reason.status_code == 422
    assert no_reason.json()["error"]["details"] == {"reason": "reason_required"}
    done = await client.post(path, json={"new_owner_id": str(THIRD_USER_ID), "reason": "left"})
    assert done.status_code == 200, done.text
    event = await audit_event(agent.id, "ownership.transfer")
    assert event.event_metadata is not None
    assert (event.event_metadata["governance"], event.event_metadata["reason"]) == (True, "left")
    async with TestSession() as session:  # owner tuple swap queued behind an 8.6 marker
        marker = await session.scalar(
            select(AuthzRevocation).where(
                AuthzRevocation.scope == "ownership",
                AuthzRevocation.object == f"agent:{agent.id}",
                AuthzRevocation.subject == f"user:{OTHER_USER_ID}",
            )
        )
    assert marker is not None


async def test_enforced_viewer_cannot_transfer(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    authz.use(allow(("can_use", obj), ("can_view", obj)))
    body = {"new_owner_id": str(TEST_USER_ID), "reason": "x"}
    forbidden = await client.post(f"{base('tool', tool.id)}/transfer", json=body)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_required"
    assert await _owner(tool.id) == OTHER_USER_ID
