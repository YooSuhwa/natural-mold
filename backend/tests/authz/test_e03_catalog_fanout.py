"""E-03 catalog fanout through GrantService/outbox (plan 7.2, 7.2.1, 10.3, S09)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import organization_fanout, sync_fanout, sync_organization
from app.authz.catalog_fanout_backfill import backfill_catalog_fanout
from app.authz.fake import FakeTupleStore
from app.authz.outbox import apply_now, process_outbox
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.organization import Organization, Tenant
from tests.authz.e03_support import (
    active_members,
    can_use,
    seed_catalog,
)
from tests.authz.grant_testkit import Clock, seed_world, service
from tests.conftest import TestSession


async def _fanout_grants(db: AsyncSession, org_id) -> list[ResourceGrant]:
    found = await db.scalars(
        select(ResourceGrant).where(
            ResourceGrant.org_id == org_id, ResourceGrant.grant_kind == "system_fanout"
        )
    )
    return list(found)


async def test_turning_an_item_off_revokes_can_use_for_every_member(db: AsyncSession) -> None:
    # Given: a fanned-out catalog in one organization with two active members.
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    catalog = await seed_catalog(db)
    grants = service(store, clock)
    org = await db.get(Organization, world.ctx.org_id)
    assert org is not None
    change = await sync_fanout(db, world.ctx, await organization_fanout(db, org), service=grants)
    await db.commit()
    await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    members = await active_members(db, world.ctx.org_id)
    assert len(members) == 2
    for obj in catalog.objects().values():
        assert can_use(store, world.ctx.org_id, obj)
    assert not can_use(store, world.ctx.org_id, f"llm_model:{catalog.hidden_model}")
    # When: the organization turns the tool and the model off.
    org.settings = {
        "catalog_policy": {
            "items": [
                {"type": "tool", "id": str(catalog.tool), "enabled": False},
                {"type": "model", "id": str(catalog.model), "enabled": False},
            ]
        }
    }
    await db.flush()
    change = await sync_fanout(db, world.ctx, await organization_fanout(db, org), service=grants)
    await db.commit()
    run = await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    # Then: no member keeps can_use on them; the MCP server stays usable.
    assert run.failed == ()
    objects = catalog.objects()
    assert not can_use(store, world.ctx.org_id, objects["tool"])
    assert not can_use(store, world.ctx.org_id, objects["model"])
    assert can_use(store, world.ctx.org_id, objects["mcp_server"])
    off = {catalog.tool, catalog.model}
    states = {g.state for g in await _fanout_grants(db, world.ctx.org_id) if g.resource_id in off}
    assert states == {"revoked"}


async def test_company_policy_narrows_and_other_organizations_keep_their_fanout(
    db: AsyncSession,
) -> None:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    catalog = await seed_catalog(db)
    tenant = await db.get(Tenant, world.tenant)
    assert tenant is not None
    tenant.settings = {
        "catalog_policy": {"items": [{"type": "tool", "id": str(catalog.tool), "enabled": False}]}
    }
    await db.commit()
    grants = service(store, clock)
    for ctx in (world.ctx, world.other_ctx):
        org = await db.get(Organization, ctx.org_id)
        assert org is not None
        change = await sync_fanout(db, ctx, await organization_fanout(db, org), service=grants)
        await db.commit()
        await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    for ctx in (world.ctx, world.other_ctx):
        assert not can_use(store, ctx.org_id, f"tool:{catalog.tool}")
        assert can_use(store, ctx.org_id, f"mcp_server:{catalog.server}")


async def test_backfill_is_idempotent_and_queues_through_the_outbox(db: AsyncSession) -> None:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    catalog = await seed_catalog(db)
    first = await backfill_catalog_fanout(TestSession, world.ctx.user_id, tenant_id=world.tenant)
    # tool, server: allowed_org + consumer; 2 visible models: consumer; two organizations.
    assert (first.organizations, first.queued) == (2, 12)
    await process_outbox(TestSession, store, now=clock.tick())
    for ctx in (world.ctx, world.other_ctx):
        assert all(can_use(store, ctx.org_id, obj) for obj in catalog.objects().values())
    again = await backfill_catalog_fanout(TestSession, world.ctx.user_id, org_id=world.ctx.org_id)
    assert (again.organizations, again.queued) == (1, 0)


async def test_legacy_request_hook_writes_nothing(db: AsyncSession) -> None:
    world = await seed_world(db)
    await seed_catalog(db)
    org = await db.get(Organization, world.ctx.org_id)
    assert org is not None
    change = await sync_organization(db, org, world.ctx.user_id, mode="legacy")
    await db.commit()
    assert change.outbox_ids == ()
    assert await db.scalar(select(func.count()).select_from(ResourceGrant)) == 0
    assert await db.scalar(select(func.count()).select_from(AuthzOutbox)) == 0
    enforced = await sync_organization(db, org, world.ctx.user_id, mode="enforce")
    await db.commit()
    assert len(enforced.outbox_ids) == 6
