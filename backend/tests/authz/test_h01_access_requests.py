"""H-01: create, dedupe, limit, cancel and expire access requests (plan 8.4, 9.7, 10.9)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.access_request_rules import MAX_PER_HOUR
from app.models.access_request import AccessRequest
from tests.authz.d04_support import AuthzSwitch, Unavailable, allow, authz
from tests.authz.e01_support import OTHER_ORG_ID, OTHER_USER_ID, add_tool, seed
from tests.authz.h01_support import PATH, acting_as, body, request_actions, requests
from tests.conftest import TestSession

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def test_hidden_missing_and_foreign_resources_answer_202_and_write_nothing(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    hidden = await add_tool(db, owner=OTHER_USER_ID)
    foreign = await add_tool(db, owner=OTHER_USER_ID, org=OTHER_ORG_ID)
    authz.use(allow(("can_use", f"tool:{foreign.id}")))  # a tuple cannot cross orgs
    for target in (hidden.id, foreign.id, OTHER_USER_ID):
        response = await client.post(PATH, json=body("tool", target))
        assert (response.status_code, response.content) == (202, b"")
    assert await requests() == []
    assert await request_actions() == []


async def test_legacy_non_owner_gets_202_and_owner_already_holds_every_level(
    client: AsyncClient, db: AsyncSession
) -> None:
    foreign_owned = await add_tool(db, owner=OTHER_USER_ID)
    mine = await add_tool(db)
    assert (await client.post(PATH, json=body("tool", foreign_owned.id))).status_code == 202
    owned = await client.post(PATH, json=body("tool", mine.id))
    assert owned.status_code == 422
    assert owned.json()["error"]["details"] == {"reason": "already_granted"}


async def test_visible_member_creates_a_pending_request_and_duplicates_return_it(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(allow(("can_use", f"tool:{tool.id}")))
    first = await client.post(PATH, json=body("tool", tool.id, reason="분기 리포트 수정"))
    assert first.status_code == 201, first.text
    view = first.json()
    assert (view["status"], view["level"], view["resource_name"]) == ("pending", "editor", "t")
    again = await client.post(PATH, json=body("tool", tool.id))
    assert (again.status_code, again.json()["id"]) == (200, view["id"])
    other_level = await client.post(PATH, json=body("tool", tool.id, "viewer"))
    assert other_level.status_code == 201
    [row, _] = await requests()
    created = row.created_at.replace(tzinfo=UTC)
    assert row.expires_at.replace(tzinfo=UTC) - created == timedelta(days=30)
    assert await request_actions() == ["access_request.create", "access_request.create"]


async def test_held_levels_and_manager_are_not_requestable(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    authz.use(allow(("can_use", obj), ("can_view", obj)))
    held = await client.post(PATH, json=body("tool", tool.id, "viewer"))
    assert held.json()["error"]["details"] == {"reason": "already_granted"}
    assert (await client.post(PATH, json=body("tool", tool.id, "manager"))).status_code == 422
    assert (await client.post(PATH, json=body("credential", tool.id))).status_code == 422


async def test_openfga_outage_answers_202(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(client=Unavailable())
    assert (await client.post(PATH, json=body("tool", tool.id))).status_code == 202


async def test_hourly_limit_answers_429(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tools = [await add_tool(db, owner=OTHER_USER_ID, name=f"t{i}") for i in range(11)]
    authz.use(allow(*[("can_use", f"tool:{tool.id}") for tool in tools]))
    for tool in tools[:MAX_PER_HOUR]:
        assert (await client.post(PATH, json=body("tool", tool.id))).status_code == 201
    limited = await client.post(PATH, json=body("tool", tools[-1].id))
    assert limited.status_code == 429
    assert limited.json()["error"]["details"]["kind"] == "rate"


async def test_requester_cancels_once_and_others_get_404(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(allow(("can_use", f"tool:{tool.id}")))
    request_id = (await client.post(PATH, json=body("tool", tool.id))).json()["id"]
    with acting_as(test_app, OTHER_USER_ID):
        assert (await client.post(f"{PATH}/{request_id}/cancel")).status_code == 404
    cancelled = await client.post(f"{PATH}/{request_id}/cancel")
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")
    closed = await client.post(f"{PATH}/{request_id}/cancel")
    assert (closed.status_code, closed.json()["error"]["code"]) == (409, "access_request_closed")
    assert await request_actions() == ["access_request.create", "access_request.cancel"]


async def test_requests_expire_after_30_days_and_may_be_sent_again(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(allow(("can_use", f"tool:{tool.id}")))
    first = (await client.post(PATH, json=body("tool", tool.id))).json()["id"]
    async with TestSession() as session:
        past = datetime.now(UTC) - timedelta(minutes=1)
        await session.execute(update(AccessRequest).values(expires_at=past))
        await session.commit()
    outbox = (await client.get(PATH, params={"box": "outbox"})).json()["items"]
    assert [(item["id"], item["status"]) for item in outbox] == [(first, "expired")]
    again = await client.post(PATH, json=body("tool", tool.id))
    assert again.status_code == 201
    assert again.json()["id"] != first
