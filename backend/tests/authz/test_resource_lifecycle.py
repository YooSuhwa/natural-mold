"""P3 foundation: resource lifecycle hooks queue 7.2.1 tuples only outside legacy mode."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeAuthz, FakeTupleStore
from app.authz.grants import GrantRequest, GrantTarget
from app.authz.outbox import process_outbox
from app.authz.resource_lifecycle import (
    LifecycleChange,
    apply_lifecycle,
    on_owner_changed,
    on_resource_created,
    on_resource_deleted,
)
from app.authz.service import AuthzService
from app.authz.tuples import RelationTuple
from app.models.agent import Agent
from app.models.authz import AuthzOutbox, AuthzRevocation, ResourceGrant
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from tests.authz.grant_testkit import Clock, World, count, seed_world, service
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession

ACTIVE = pytest.mark.parametrize("mode", ["shadow", "enforce"])


def _authz(mode: str) -> AuthzService:
    return AuthzService(FakeAuthz(), mode=mode)  # type: ignore[arg-type]


async def _new_agent(db: AsyncSession, world: World) -> Agent:
    agent = Agent(name="n", system_prompt="p", user_id=world.ctx.user_id, model_id=world.model)
    agent.org_id, agent.tenant_id = world.ctx.org_id, world.tenant
    db.add(agent)
    await db.flush()
    return agent


def _agent_tuples(agent: Agent, owner) -> set[RelationTuple]:
    obj = f"agent:{agent.id}"
    return {
        RelationTuple(f"organization:{agent.org_id}", "org", obj),
        RelationTuple(f"user:{owner}", "owner", obj),
        RelationTuple(obj, "resident_agent", f"organization:{agent.org_id}"),
    }


async def _apply(change: LifecycleChange, store: FakeTupleStore, mode: str):
    return await apply_lifecycle(change, _authz(mode), writer=store, sessions=TestSession)


async def _created(db: AsyncSession, row, store: FakeTupleStore, mode) -> None:
    change = await on_resource_created(db, row, mode=mode)
    await db.commit()
    await _apply(change, store, mode)


async def test_legacy_mode_queues_nothing_and_never_flushes(db: AsyncSession):
    world = await seed_world(db)
    agent = Agent(name="n", system_prompt="p", user_id=world.ctx.user_id, model_id=world.model)
    agent.org_id, agent.tenant_id = world.ctx.org_id, world.tenant
    db.add(agent)
    for mode in ("legacy", None):  # explicit and the default AUTHZ_MODE=legacy
        assert await on_resource_created(db, agent, mode=mode) == LifecycleChange()
        assert await on_resource_deleted(db, agent, mode=mode) == LifecycleChange()
    assert agent in db.new  # legacy hooks did not even flush the pending row
    await db.commit()
    assert await count(db, AuthzOutbox) == 0
    store = FakeTupleStore()
    assert (await _apply(LifecycleChange(), store, "legacy")).processed == ()
    assert store.stored() == set()


@ACTIVE
async def test_create_queues_tuples_that_land_only_after_apply(db: AsyncSession, mode):
    world, store = await seed_world(db), FakeTupleStore()
    agent = await _new_agent(db, world)
    change = await on_resource_created(db, agent, mode=mode)
    await db.commit()
    assert len(change.outbox_ids) == 3 and store.stored() == set()
    run = await _apply(change, store, mode)
    assert set(run.processed) == set(change.outbox_ids)
    assert store.stored() == _agent_tuples(agent, world.ctx.user_id)


@ACTIVE
async def test_rollback_drops_the_queued_rows(db: AsyncSession, mode):
    world = await seed_world(db)
    change = await on_resource_created(db, await _new_agent(db, world), mode=mode)
    await db.rollback()
    assert change.outbox_ids and await count(db, AuthzOutbox) == 0


@ACTIVE
async def test_delete_removes_own_and_grant_tuples_behind_a_resource_marker(db: AsyncSession, mode):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    agent = await _new_agent(db, world)
    await _created(db, agent, store, mode)
    grants = service(store, clock)
    shared = await grants.grant(
        db, world.ctx, GrantTarget("agent", agent.id), GrantRequest("viewer", "user", world.member)
    )
    await db.commit()
    await grants.apply(shared)
    assert len(store.stored()) == 4
    change = await on_resource_deleted(db, agent, mode=mode)
    await db.delete(agent)
    await db.commit()
    marker = await db.scalar(select(AuthzRevocation).where(AuthzRevocation.scope == "resource"))
    assert marker is not None and marker.object == f"agent:{agent.id}" and marker.subject == "*"
    assert len(change.outbox_ids) == 4 and len(store.stored()) == 4  # nothing until drained
    await process_outbox(TestSession, store)
    assert store.stored() == set()
    grant = await db.get(ResourceGrant, shared.grant.id, populate_existing=True)
    await db.refresh(marker)
    assert grant is not None and grant.state == "revoked" and marker.finalized_at is not None


@ACTIVE
async def test_owner_change_swaps_only_the_owner_tuple(db: AsyncSession, mode):
    world, store = await seed_world(db), FakeTupleStore()
    agent = await _new_agent(db, world)
    await _created(db, agent, store, mode)
    previous, agent.user_id = agent.user_id, world.member
    change = await on_owner_changed(db, agent, previous_owner_id=previous, mode=mode)
    await db.commit()
    assert len(change.outbox_ids) == 2
    await _apply(change, store, mode)
    assert store.stored() == _agent_tuples(agent, world.member)
    marker = await db.scalar(select(AuthzRevocation).where(AuthzRevocation.scope == "ownership"))
    assert marker is not None and marker.subject == f"user:{previous}"


@ACTIVE
async def test_mcp_server_delete_includes_its_tool_server_tuples(db: AsyncSession, mode):
    world, store = await seed_world(db), FakeTupleStore()
    server = McpServer(user_id=world.ctx.user_id, name="m", transport="sse", url="https://x.test")
    server.org_id, server.tenant_id = world.ctx.org_id, world.tenant
    db.add(server)
    await db.flush()
    tool = McpTool(server_id=server.id, name="t", input_schema={})
    db.add(tool)
    await db.flush()
    for row in (server, tool):
        await _created(db, row, store, mode)
    assert RelationTuple(f"mcp_server:{server.id}", "server", f"mcp_tool:{tool.id}") in (
        store.stored()
    )
    change = await on_resource_deleted(db, server, mode=mode)
    await db.commit()
    await _apply(change, store, mode)
    assert store.stored() == set()
