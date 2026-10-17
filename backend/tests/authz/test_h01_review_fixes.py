"""H-01 review: confirmed-only paging, side-effect-free reads, quota lock, member exit."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import access_requests
from app.models.access_request import AccessRequest
from app.models.audit_event import AuditEvent
from tests.authz.d04_support import AuthzSwitch, authz
from tests.authz.e01_support import OTHER_USER_ID, TEST_USER_ID, add_tool
from tests.authz.e02_support import delete, seed_admin
from tests.authz.h01_support import PATH, acting_as, body, requests, tuples
from tests.conftest import TestSession

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def test_inbox_pages_never_count_requests_the_caller_cannot_share(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    shareable = await add_tool(db, owner=OTHER_USER_ID, name="a")
    hidden = await add_tool(db, owner=OTHER_USER_ID, name="b")
    a, b = f"tool:{shareable.id}", f"tool:{hidden.id}"
    authz.use(tuples(TEST_USER_ID, ("can_use", a), ("can_use", b)))
    for tool in (shareable, hidden):  # the hidden request is the newest
        assert (await client.post(PATH, json=body("tool", tool.id))).status_code == 201
    authz.use(tuples(OTHER_USER_ID, ("can_use", a), ("can_share", a)))
    with acting_as(test_app, OTHER_USER_ID):
        page = (await client.get(PATH, params={"box": "inbox", "limit": 1})).json()
    assert [item["resource_id"] for item in page["items"]] == [str(shareable.id)]
    assert (page["has_more"], page["next_cursor"]) == (False, None)


async def test_reads_show_expiry_without_writing_it(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(tuples(TEST_USER_ID, ("can_use", f"tool:{tool.id}")))
    request_id = (await client.post(PATH, json=body("tool", tool.id))).json()["id"]
    async with TestSession() as session:
        past = datetime.now(UTC) - timedelta(minutes=1)
        await session.execute(update(AccessRequest).values(expires_at=past))
        await session.commit()
    outbox = (await client.get(PATH, params={"box": "outbox"})).json()["items"]
    assert [item["status"] for item in outbox] == ["expired"]
    authz.use(mode="legacy")
    with acting_as(test_app, OTHER_USER_ID):
        assert (await client.get(PATH, params={"box": "inbox"})).json()["items"] == []
        handled = (await client.get(PATH, params={"box": "handled"})).json()["items"]
    assert [(item["id"], item["status"]) for item in handled] == [(request_id, "expired")]
    [row] = await requests()
    assert row.status == "pending"  # nothing was updated by the reads


async def test_create_takes_the_org_lock_before_counting(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(tuples(TEST_USER_ID, ("can_use", f"tool:{tool.id}")))
    order: list[str] = []
    lock, limits = access_requests.lock_org_row, access_requests.check_limits

    async def locking(*args: object) -> None:
        order.append("lock")
        await lock(*args)  # type: ignore[arg-type]

    async def counting(*args: object) -> None:
        order.append("count")
        await limits(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(access_requests, "lock_org_row", locking)
    monkeypatch.setattr(access_requests, "check_limits", counting)
    assert (await client.post(PATH, json=body("tool", tool.id))).status_code == 201
    assert order == ["lock", "count"]


async def test_member_removal_cancels_the_leavers_pending_requests(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, test_app: FastAPI
) -> None:
    tool = await add_tool(db)  # owned by the administrating test user
    authz.use(tuples(OTHER_USER_ID, ("can_use", f"tool:{tool.id}")))
    with acting_as(test_app, OTHER_USER_ID):
        assert (await client.post(PATH, json=body("tool", tool.id))).status_code == 201
    authz.use(mode="legacy")
    response = await delete(client)
    assert response.status_code in (200, 204), response.text
    [row] = await requests()
    assert (row.status, row.decided_by) == ("cancelled", TEST_USER_ID)
    async with TestSession() as session:
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "access_request.cancel")
        )
    assert event is not None
    assert (event.event_metadata or {})["member_exit"] is True
