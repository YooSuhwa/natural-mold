"""H-02: the 14.2 call sites — access requests, shares, transfers (E-01, H-01)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from tests.authz.d04_support import AuthzSwitch, authz
from tests.authz.e01_support import OTHER_USER_ID, TEST_USER_ID, add_group, add_tool, base, seed
from tests.authz.h01_support import PATH as REQUESTS
from tests.authz.h01_support import acting_as, body, tuples
from tests.authz.h02_support import PATH, rows
from tests.conftest import TestSession

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _clear() -> None:
    async with TestSession() as session:
        await session.execute(delete(Notification))
        await session.commit()


async def _request(client: AsyncClient, authz: AuthzSwitch, tool_id) -> str:
    authz.use(tuples(TEST_USER_ID, ("can_use", f"tool:{tool_id}")))
    response = await client.post(REQUESTS, json=body("tool", tool_id, reason="비밀 사유"))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_request_notifies_the_owner_and_never_the_requester(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID, name="영업 리포트")
    await _request(client, authz, tool.id)
    await client.post(REQUESTS, json=body("tool", tool.id))  # duplicate: same request
    assert await rows(TEST_USER_ID) == []
    [row] = await rows(OTHER_USER_ID)
    assert (row.kind, row.actor_id, row.target_id) == (
        "access_request.created",
        TEST_USER_ID,
        tool.id,
    )
    assert row.payload == {"target_name": "영업 리포트", "level": "editor"}  # no reason text
    authz.use(mode="legacy")
    with acting_as(test_app, OTHER_USER_ID):
        [item] = (await client.get(PATH)).json()["items"]
    assert item["link"] == "/settings/organization/requests"
    assert item["target"]["name"] == "영업 리포트"


async def test_decisions_notify_the_requester_once(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    denied_tool = await add_tool(db, owner=OTHER_USER_ID, name="a")
    approved_tool = await add_tool(db, owner=OTHER_USER_ID, name="b")
    denied = await _request(client, authz, denied_tool.id)
    approved = await _request(client, authz, approved_tool.id)
    authz.use(mode="legacy")
    with acting_as(test_app, OTHER_USER_ID):
        assert (await client.post(f"{REQUESTS}/{denied}/deny", json={})).status_code == 200
        assert (await client.post(f"{REQUESTS}/{approved}/approve", json={})).status_code == 200
    mine = await rows(TEST_USER_ID)
    assert sorted((r.kind, r.target_id, r.payload["decision"]) for r in mine) == sorted(
        [
            ("access_request.decided", denied_tool.id, "denied"),
            ("access_request.decided", approved_tool.id, "approved"),
        ]
    )  # the approval's share sends no extra grant.received
    assert {r.actor_id for r in mine} == {OTHER_USER_ID}


async def test_share_to_a_user_notifies_them_and_group_shares_do_not(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db, name="인사 도우미")
    share = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "consumer"}
    assert (await client.post(f"{base('tool', tool.id)}/grants", json=share)).status_code == 201
    [row] = await rows(OTHER_USER_ID)
    assert (row.kind, row.actor_id, row.payload["level"]) == (
        "grant.received",
        TEST_USER_ID,
        "consumer",
    )
    await _clear()
    again = await client.post(f"{base('tool', tool.id)}/grants", json=share)  # no-op re-share
    assert again.status_code in (200, 201)
    assert await rows(OTHER_USER_ID) == []
    share["level"] = "viewer"  # a level change is a new grant
    await client.post(f"{base('tool', tool.id)}/grants", json=share)
    assert [r.payload["level"] for r in await rows(OTHER_USER_ID)] == ["viewer"]
    await _clear()
    group = await add_group(db, members=(OTHER_USER_ID,))
    to_group = {"principal": {"type": "group", "id": str(group)}, "level": "viewer"}
    response = await client.post(f"{base('tool', tool.id)}/grants", json=to_group)
    assert response.status_code == 201
    assert await rows(OTHER_USER_ID) == []


async def test_transfer_notifies_the_new_owner(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db, name="t")
    payload = {"new_owner_id": str(OTHER_USER_ID), "keep_manager": False}
    response = await client.post(f"{base('tool', tool.id)}/transfer", json=payload)
    assert response.status_code == 200, response.text
    [row] = await rows(OTHER_USER_ID)
    assert (row.kind, row.target_id, row.payload["role"], row.link) == (
        "ownership.transferred",
        tool.id,
        "new_owner",
        "/tools",
    )
    assert await rows(TEST_USER_ID) == []  # the owner moved it themselves
