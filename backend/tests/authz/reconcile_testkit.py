"""C-07 fixture: the C-04 grant world plus every other 7.2 source table, projected through
the real outbox into a ``FakeTupleStore`` (the "previous fixture" of the completion check).
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest
from app.authz.tuple_types import RelationTuple
from app.models.authz import AuthzOutbox, AuthzRevocation
from app.models.credential import Credential
from app.models.mcp_tool import McpTool
from app.models.organization import OrganizationRoleGrant, TenantMember, TenantRoleGrant
from app.models.organization_group import GroupMember, OrgCapabilityGrant
from tests.authz.grant_testkit import (
    START,
    Clock,
    World,
    agent_target,
    mcp_target,
    seed_world,
    service,
)


@dataclass(frozen=True, slots=True)
class Fixture:
    world: World
    store: FakeTupleStore
    clock: Clock
    agent: UUID  # carries the outbox-projected grants
    share: RelationTuple  # user viewer grant (unconditioned)
    expiring: RelationTuple  # agent#consumer with ``non_expired_grant``
    share_grant: UUID  # the ``resource_grants`` row behind ``share``


async def seed_fixture(db: AsyncSession) -> Fixture:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    actor, org, tenant = world.ctx.user_id, world.ctx.org_id, world.tenant
    db.add_all(
        [
            *(TenantMember(tenant_id=tenant, user_id=u) for u in (actor, world.member)),
            TenantMember(tenant_id=tenant, user_id=world.outsider),
            TenantRoleGrant(tenant_id=tenant, user_id=actor, role="admin"),
            OrganizationRoleGrant(
                org_id=org,
                tenant_id=tenant,
                role="admin",
                principal_type="user",
                principal_id=actor,
            ),
            GroupMember(group_id=world.group, org_id=org, tenant_id=tenant, user_id=world.member),
            OrgCapabilityGrant(
                org_id=org,
                tenant_id=tenant,
                capability="cap_create_agent",
                principal_type="organization",
                principal_id=org,
            ),
            Credential(
                user_id=actor,
                org_id=org,
                tenant_id=tenant,
                name="c",
                definition_key="openai_api",
                data_encrypted="dummy",
                key_id="dummy",
                field_keys=[],
            ),
        ]
    )
    await db.commit()
    server = await mcp_target(db, world)
    db.add(McpTool(server_id=server.resource_id, name="lookup"))
    await db.commit()
    target = await agent_target(db, world)
    grants = service(store, clock)
    requests = [
        GrantRequest("viewer", "user", world.member),
        GrantRequest("viewer", "group", world.group),
        GrantRequest("consumer", "user", world.member, expires_at=START + timedelta(days=1)),
    ]
    changes = []
    for request in requests:
        changes.append(await grants.grant(db, world.ctx, target, request))
        await db.commit()
        await grants.apply(changes[-1])
    obj = f"agent:{target.resource_id}"
    share = RelationTuple(f"user:{world.member}", "viewer", obj)
    [expiring] = [t for t in store.stored() if t.relation == "consumer"]
    share_grant = changes[0].grant.id
    return Fixture(world, store, clock, target.resource_id, share, expiring, share_grant)


async def age_activity(db: AsyncSession) -> None:
    """Move all outbox/marker activity a day before ``START`` (outside any hold window)."""
    old = START - timedelta(days=1)
    await db.execute(update(AuthzOutbox).values(created_at=old, processed_at=old))
    await db.execute(
        update(AuthzRevocation)
        .where(AuthzRevocation.finalized_at.is_not(None))
        .values(finalized_at=old, created_at=old)
    )
    await db.commit()


def drift(fx: Fixture) -> RelationTuple:
    """Remove one tuple, add a stray one, change one condition; returns the stray tuple."""
    stray = RelationTuple(f"user:{fx.world.outsider}", "viewer", f"agent:{fx.agent}")
    key = (fx.expiring.user, fx.expiring.relation, fx.expiring.object)
    fx.store.tuples.pop((fx.share.user, fx.share.relation, fx.share.object))
    fx.store.tuples[(stray.user, stray.relation, stray.object)] = stray
    fx.store.tuples[key] = RelationTuple.of(*key, "non_expired_grant", {"grant_time": "x"})
    return stray
