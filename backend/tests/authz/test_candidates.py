"""P3 foundation: list candidates = owned ∪ granted (me, my groups, my org) ∪ platform."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.candidates import candidate_ids
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.organization_group import GroupMember
from app.models.tool import Tool
from tests.authz.grant_testkit import World, seed_world


async def _agent(db: AsyncSession, world: World, ctx: OrgContext) -> Agent:
    agent = Agent(name="c", system_prompt="p", user_id=ctx.user_id, model_id=world.model)
    agent.org_id, agent.tenant_id = ctx.org_id, ctx.tenant_id
    db.add(agent)
    await db.flush()
    return agent


def _grant(world: World, agent: Agent, kind: str, principal, state: str = "active"):
    return ResourceGrant(
        org_id=agent.org_id,
        tenant_id=world.tenant,
        resource_type="agent",
        resource_id=agent.id,
        relation="viewer",
        principal_type=kind,
        principal_id=principal,
        state=state,
    )


async def test_agent_candidates_cover_owned_and_every_grant_path(db: AsyncSession):
    world = await seed_world(db)
    me = OrgContext(org_id=world.ctx.org_id, tenant_id=world.tenant, user_id=world.member)
    db.add(
        GroupMember(
            group_id=world.group, org_id=me.org_id, tenant_id=world.tenant, user_id=me.user_id
        )
    )
    owned = await _agent(db, world, me)
    by_user, by_group, by_org, revoked, hidden = [
        await _agent(db, world, world.ctx) for _ in range(5)
    ]
    other_org = await _agent(db, world, world.other_ctx)
    db.add_all(
        [
            _grant(world, by_user, "user", me.user_id),
            _grant(world, by_group, "group", world.group),
            _grant(world, by_org, "organization", me.org_id),
            _grant(world, revoked, "user", me.user_id, state="revoked"),
            _grant(world, hidden, "user", uuid4()),
        ]
    )
    await db.commit()
    found = set(await candidate_ids(db, Agent, "agent", me))
    assert found == {owned.id, by_user.id, by_group.id, by_org.id}
    assert other_org.id not in found and world.agent not in found
    foreign = OrgContext(org_id=me.org_id, tenant_id=uuid4(), user_id=me.user_id)
    assert await candidate_ids(db, Agent, "agent", foreign) == []


async def test_platform_rows_join_only_when_requested(db: AsyncSession):
    world = await seed_world(db)
    system = Tool(definition_key="k", name="sys", is_system=True)
    mine = Tool(definition_key="k", name="mine", user_id=world.ctx.user_id)
    mine.org_id, mine.tenant_id = world.ctx.org_id, world.tenant
    db.add_all([system, mine])
    await db.commit()
    assert await candidate_ids(db, Tool, "tool", world.ctx) == [mine.id]
    both = await candidate_ids(db, Tool, "tool", world.ctx, platform=True)
    assert set(both) == {system.id, mine.id}
    with pytest.raises(ValueError, match="no platform"):
        await candidate_ids(db, Agent, "agent", world.ctx, platform=True)
