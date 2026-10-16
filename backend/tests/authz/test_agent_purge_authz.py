"""D-02: an Agent purge queues tuple deletes behind a resource marker (plan 7.2.1, 8.6)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.authz import AuthzOutbox, AuthzRevocation
from app.services.agent_purge_authz import queue_agent_tuple_deletes
from tests.authz.agent_route_fixture import seed_agent
from tests.conftest import TEST_USER_ID, TestSession


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_purge_queues_delete_rows_and_a_resource_marker(mode):
    agent_id = uuid.UUID(await seed_agent(owner=TEST_USER_ID))
    async with TestSession() as db:
        change = await queue_agent_tuple_deletes(db, agent_id, mode=mode)
        await db.commit()
        ops = set(await db.scalars(select(AuthzOutbox.op)))
        marker = await db.scalar(select(AuthzRevocation))
    assert change.outbox_ids and ops == {"delete"}
    assert marker is not None and marker.scope == "resource"
    assert marker.object == f"agent:{agent_id}" and marker.subject == "*"


async def test_legacy_purge_queues_nothing():
    agent_id = uuid.UUID(await seed_agent(owner=TEST_USER_ID))
    async with TestSession() as db:
        change = await queue_agent_tuple_deletes(db, agent_id, mode="legacy")
        await db.commit()
        rows = await db.scalar(select(func.count()).select_from(AuthzOutbox))
        markers = await db.scalar(select(func.count()).select_from(AuthzRevocation))
    assert change.outbox_ids == () and rows == 0 and markers == 0
