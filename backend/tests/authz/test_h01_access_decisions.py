"""H-01: inbox, approve (E-01 share path), deny, and decision enumeration safety (S18)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.d04_support import AuthzSwitch, authz
from tests.authz.e01_support import (
    FOREIGN_USER_ID,
    OTHER_USER_ID,
    TEST_USER_ID,
    add_skill,
    add_tool,
    audit_actions,
    grants,
    seed,
)
from tests.authz.h01_support import PATH, acting_as, body, request_actions, tuples

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _request(client: AsyncClient, authz: AuthzSwitch, kind: str, rid, level="editor"):
    """The test user (``can_use`` only) asks for ``level``; returns the request id."""
    authz.use(tuples(TEST_USER_ID, ("can_use", f"{kind}:{rid}")))
    response = await client.post(PATH, json=body(kind, rid, level, reason="양식 수정"))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_owner_sees_the_inbox_approves_and_the_requester_gets_the_grant(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    skill = await add_skill(db, owner=OTHER_USER_ID, slug="report")
    request_id = await _request(client, authz, "skill", skill.id)
    authz.use(mode="legacy")  # the owner decides with the legacy answer
    with acting_as(test_app, OTHER_USER_ID):
        inbox = (await client.get(PATH, params={"box": "inbox"})).json()
        assert [item["id"] for item in inbox["items"]] == [request_id]
        assert inbox["items"][0]["requester"]["id"] == str(TEST_USER_ID)
        approved = await client.post(f"{PATH}/{request_id}/approve", json={"note": "ok"})
        assert approved.status_code == 200, approved.text
        again = await client.post(f"{PATH}/{request_id}/approve", json={})
        assert again.json()["error"]["code"] == "access_request_closed"
        handled = (await client.get(PATH, params={"box": "handled"})).json()["items"]
        assert [(i["id"], i["status"]) for i in handled] == [(request_id, "approved")]
    assert (approved.json()["status"], approved.json()["granted_level"]) == ("approved", "editor")
    [grant] = await grants(skill.id, state="active")
    assert (grant.principal_id, grant.relation, grant.granted_by) == (
        TEST_USER_ID,
        "editor",
        OTHER_USER_ID,
    )
    assert await audit_actions(skill.id) == ["grant.create"]
    assert await request_actions() == ["access_request.create", "access_request.approve"]
    outbox = (await client.get(PATH, params={"box": "outbox"})).json()["items"]
    assert [item["status"] for item in outbox] == ["approved"]


async def test_enforced_manager_approves_a_lower_level_but_never_a_higher_one(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    request_id = await _request(client, authz, "tool", tool.id, "viewer")
    authz.use(tuples(FOREIGN_USER_ID, ("can_use", obj), ("can_share", obj)))
    with acting_as(test_app, OTHER_USER_ID):  # no tuples: the owner is a stranger here
        assert (await client.post(f"{PATH}/{request_id}/approve", json={})).status_code == 404
    authz.use(tuples(OTHER_USER_ID, ("can_use", obj), ("can_share", obj)))
    with acting_as(test_app, OTHER_USER_ID):
        raised = await client.post(f"{PATH}/{request_id}/approve", json={"level": "editor"})
        assert raised.status_code == 422
        lowered = await client.post(f"{PATH}/{request_id}/approve", json={"level": "consumer"})
    assert lowered.json()["granted_level"] == "consumer"
    [grant] = await grants(tool.id, state="active")
    assert (grant.principal_id, grant.relation) == (TEST_USER_ID, "consumer")


async def test_deny_closes_the_request_with_the_note_and_writes_no_grant(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    request_id = await _request(client, authz, "tool", tool.id)
    authz.use(mode="legacy")
    with acting_as(test_app, OTHER_USER_ID):
        denied = await client.post(f"{PATH}/{request_id}/deny", json={"note": "보안 검토 필요"})
    assert denied.status_code == 200
    assert (denied.json()["status"], denied.json()["decision_note"]) == ("denied", "보안 검토 필요")
    assert await grants(tool.id) == []
    assert await request_actions() == ["access_request.create", "access_request.deny"]


async def test_requester_and_strangers_cannot_decide_or_see_the_inbox(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    request_id = await _request(client, authz, "tool", tool.id)
    for action in ("approve", "deny"):  # the requester only holds can_use
        assert (await client.post(f"{PATH}/{request_id}/{action}", json={})).status_code == 404
    assert (await client.post(f"{PATH}/{OTHER_USER_ID}/approve", json={})).status_code == 404
    assert (await client.get(PATH, params={"box": "inbox"})).json()["items"] == []
    authz.use(mode="legacy")
    with acting_as(test_app, FOREIGN_USER_ID):  # a non-owner: legacy hides the resource
        assert (await client.post(f"{PATH}/{request_id}/deny", json={})).status_code == 404
    assert await grants(tool.id) == []


async def test_invalid_cursor_is_422(client: AsyncClient) -> None:
    response = await client.get(PATH, params={"box": "outbox", "cursor": "%%%"})
    assert response.status_code == 422
