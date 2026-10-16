"""16.4 revocation order (8.6), shared keys, in-place narrowing and per-key queue order."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest, GrantTarget
from app.authz.outbox import process_outbox
from app.authz.revocations import count_pending
from app.authz.tuples import RelationTuple
from app.models.authz import AuthzRevocation, ResourceGrant
from tests.authz.grant_testkit import (
    Clock,
    World,
    agent_target,
    count,
    outbox_rows,
    seed_world,
    service,
    system_item,
)
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


def viewer(principal) -> GrantRequest:
    return GrantRequest("viewer", "user", principal)


async def test_revoke_keeps_denying_until_the_delete_is_confirmed(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants = service(store, clock)
    change = await grants.grant(db, world.ctx, await agent_target(db, world), viewer(world.member))
    await db.commit()
    await grants.apply(change)
    grant_id = change.grant.id
    revoke = await grants.revoke(db, world.ctx, grant_id)
    await db.commit()
    grant = await db.get(ResourceGrant, grant_id, populate_existing=True)
    assert grant is not None and grant.state == "revoking" and await count_pending(db) == 1
    [marker] = (await db.scalars(select(AuthzRevocation))).all()
    assert marker.subject == f"user:{world.member}" and marker.scope == "grant"
    store.fail_calls = 1
    assert (await grants.apply(revoke)).failed == revoke.outbox_ids
    assert len(store.stored()) == 1 and await count_pending(db) == 1
    run = await process_outbox(TestSession, store, now=clock.tick(5))
    assert run.processed == revoke.outbox_ids and store.stored() == set()
    await db.refresh(grant)
    await db.refresh(marker)
    assert grant.state == "revoked" and grant.revoked_at is not None
    assert marker.finalized_at is not None and await count_pending(db) == 0


async def test_revoke_rolled_back_keeps_grant_and_writes_no_marker(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants = service(store, clock)
    change = await grants.grant(db, world.ctx, await agent_target(db, world), viewer(world.member))
    await db.commit()
    grant_id = change.grant.id
    await grants.revoke(db, world.ctx, grant_id)
    await db.rollback()
    grant = await db.get(ResourceGrant, grant_id, populate_existing=True)
    assert grant is not None and grant.state == "active"
    assert await count(db, AuthzRevocation) == 0 and len(await outbox_rows(db)) == 1


async def _fanouts(db: AsyncSession, world: World, store: FakeTupleStore, clock: Clock):
    grants, item = service(store, clock), await system_item(db)
    changes = []
    for ctx in (world.ctx, world.other_ctx):
        request = GrantRequest("installer", "organization", ctx.org_id, grant_kind="system_fanout")
        changes.append(await grants.grant(db, ctx, GrantTarget("marketplace_item", item), request))
        await db.commit()
    for change in changes:
        await grants.apply(change)
    tenant = RelationTuple(f"tenant:{world.tenant}", "tenant", f"marketplace_item:{item}")
    assert tenant in store.stored() and len(store.stored()) == 3
    return grants, changes, tenant


async def test_shared_key_is_recomputed_from_the_remaining_rows(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, changes, tenant = await _fanouts(db, world, store, clock)
    first = await grants.revoke(db, world.ctx, changes[0].grant.id)
    await db.commit()
    assert [row.op for row in await outbox_rows(db) if row.id in first.outbox_ids] == ["delete"]
    await grants.apply(first)
    assert tenant in store.stored() and len(store.stored()) == 2
    last = await grants.revoke(db, world.other_ctx, changes[1].grant.id)
    await db.commit()
    await grants.apply(last)
    assert store.stored() == set() and await count_pending(db) == 0


async def test_back_to_back_revokes_of_a_shared_key_delete_it(db: AsyncSession):
    # Sequential stand-in for two concurrent revokes serialized by ``lock_object``: the
    # second sees the first as ``revoking``, so the last revoke deletes the tenant tuple.
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, changes, tenant = await _fanouts(db, world, store, clock)
    revokes = []
    for ctx, change in zip((world.ctx, world.other_ctx), changes, strict=True):
        revokes.append(await grants.revoke(db, ctx, change.grant.id))
        await db.commit()
    queued = {row.tuple_key for row in await outbox_rows(db) if row.op == "delete"}
    assert f"{tenant.user} tenant {tenant.object}" in queued
    await process_outbox(TestSession, store, now=clock.tick())
    assert store.stored() == set() and await count_pending(db) == 0


async def test_revocation_closes_only_after_every_key_is_deleted(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, item = service(store, clock), await system_item(db)
    request = GrantRequest(
        "installer", "organization", world.ctx.org_id, grant_kind="system_fanout"
    )
    change = await grants.grant(db, world.ctx, GrantTarget("marketplace_item", item), request)
    await db.commit()
    await grants.apply(change)
    revoke = await grants.revoke(db, world.ctx, change.grant.id)
    await db.commit()
    store.fail_calls = 1  # one of the two deletes (principal and tenant keys) fails
    run = await grants.apply(revoke)
    assert len(run.processed) == 1 and len(run.failed) == 1
    grant = await db.get(ResourceGrant, change.grant.id, populate_existing=True)
    assert grant is not None and grant.state == "revoking" and await count_pending(db) == 1
    await process_outbox(TestSession, store, now=clock.tick(5))
    await db.refresh(grant)
    assert grant.state == "revoked" and await count_pending(db) == 0


async def test_narrowing_in_place_denies_until_the_rewrite_lands(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    first = await grants.grant(
        db, world.ctx, target, GrantRequest("consumer", "user", world.member)
    )
    await db.commit()
    await grants.apply(first)
    clock.tick()
    shorter = GrantRequest("consumer", "user", world.member, expires_at=clock.now + timedelta(1))
    change = await grants.grant(db, world.ctx, target, shorter)
    await db.commit()
    [marker] = (await db.scalars(select(AuthzRevocation))).all()
    [row] = [row for row in await outbox_rows(db) if row.id in change.outbox_ids]
    assert row.source_table == "authz_revocations" and row.source_id == marker.id
    assert row.tuple["replace"] is True and await count_pending(db) == 1
    store.fail_calls = 1
    await grants.apply(change)
    assert await count_pending(db) == 1  # rewrite not confirmed: still denied
    await process_outbox(TestSession, store, now=clock.tick(5))
    [stored] = store.stored()
    assert stored.condition is not None and stored.condition.name == "non_expired_grant"
    assert await count_pending(db) == 0 and await count(db, ResourceGrant) == 1


async def test_a_failed_row_holds_back_later_rows_of_its_key(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    change = await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    await grants.apply(change)
    revoke = await grants.revoke(db, world.ctx, change.grant.id)
    await db.commit()
    store.fail_calls = 1
    await grants.apply(revoke)  # the delete is now in backoff
    again = await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    assert (await grants.apply(again)).deferred == again.outbox_ids
    store.fail_calls = 1  # same batch: the delete fails again, the write must wait
    run = await process_outbox(TestSession, store, now=clock.tick(5))
    assert run.failed == revoke.outbox_ids and run.deferred == again.outbox_ids
    run = await process_outbox(TestSession, store, now=clock.tick(10))
    assert run.processed == (*revoke.outbox_ids, *again.outbox_ids)
    assert len(store.stored()) == 1


async def test_a_revoke_does_not_close_a_pending_narrowing_of_the_same_pair(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    consumer = GrantRequest("consumer", "user", world.member)
    shared = await grants.grant(db, world.ctx, target, viewer(world.member))
    await grants.grant(db, world.ctx, target, consumer)
    await db.commit()
    await process_outbox(TestSession, store, now=clock.tick())
    shorter = replace(consumer, expires_at=clock.tick() + timedelta(1))
    narrowing = await grants.grant(db, world.ctx, target, shorter)
    await db.commit()
    store.fail_calls = 1
    await grants.apply(narrowing)  # the rewrite is in backoff; its marker must hold
    revoke = await grants.revoke(db, world.ctx, shared.grant.id)
    await db.commit()
    assert (await grants.apply(revoke)).processed == revoke.outbox_ids
    assert await count_pending(db) == 1  # only the revoke's marker closed
    await process_outbox(TestSession, store, now=clock.tick(5))
    assert await count_pending(db) == 0
