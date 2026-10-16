"""E-07 done-when: platform → company → organization, each tier can only narrow (7.2, S07)."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import organization_fanout, sync_fanout
from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, MODEL_ORGS_KEY
from app.authz.fake import FakeTupleStore
from app.authz.outbox import apply_now
from app.models.organization import Organization, Tenant
from tests.authz.e03_support import Catalog, can_use, seed_catalog
from tests.authz.e07_support import company_model, credential
from tests.authz.grant_testkit import Clock, World, seed_world, service
from tests.conftest import TestSession


async def _models(db: AsyncSession, org_id: UUID) -> set[UUID]:
    org = await db.get(Organization, org_id)
    assert org is not None
    keys = await organization_fanout(db, org)
    await db.rollback()  # organization_fanout locks the company row
    return {rid for kind, rid, _ in keys if kind == "llm_model"}


async def _settings(db: AsyncSession, world: World, **settings: Any) -> None:
    tenant = await db.get(Tenant, world.tenant)
    assert tenant is not None
    tenant.settings = {**(tenant.settings or {}), **settings}
    await db.commit()


async def _seed(db: AsyncSession) -> tuple[World, Catalog, UUID]:
    world = await seed_world(db)
    catalog = await seed_catalog(db)
    designated = await company_model(db, world.tenant, "Designated")
    return world, catalog, designated.id


async def test_company_model_reaches_only_designated_organizations(db: AsyncSession) -> None:
    # Given: a company model designated to the first organization only.
    world, catalog, designated = await _seed(db)
    await company_model(db, world.tenant, "Undesignated")
    await _settings(db, world, **{MODEL_ORGS_KEY: {str(designated): [str(world.ctx.org_id)]}})
    # Then: platform models reach both organizations, the company model only the first;
    # an undesignated company model and a hidden platform model reach nobody.
    first, second = await _models(db, world.ctx.org_id), await _models(db, world.other_ctx.org_id)
    assert first == {world.model, catalog.model, designated}
    assert second == {world.model, catalog.model}


async def test_each_tier_only_narrows(db: AsyncSession) -> None:
    world, catalog, designated = await _seed(db)
    first = str(world.ctx.org_id)
    # Company: the platform model is designated to the first organization only.
    await _settings(
        db,
        world,
        **{MODEL_ORGS_KEY: {str(designated): [first], str(catalog.model): [first]}},
    )
    assert catalog.model not in await _models(db, world.other_ctx.org_id)
    # Company policy cannot re-enable what the platform hides.
    hidden_on = {"type": "model", "id": str(catalog.hidden_model), "enabled": True}
    await _settings(db, world, catalog_policy={"items": [hidden_on]})
    assert catalog.hidden_model not in await _models(db, world.ctx.org_id)
    # Organization: turning the company model off removes it there only.
    org = await db.get(Organization, world.ctx.org_id)
    assert org is not None
    org.settings = {
        "catalog_policy": {"items": [{"type": "model", "id": str(designated), "enabled": False}]}
    }
    await db.commit()
    assert await _models(db, world.ctx.org_id) == {world.model, catalog.model}
    # An organization override cannot widen a company designation either.
    other = await db.get(Organization, world.other_ctx.org_id)
    assert other is not None
    other.settings = {
        "catalog_policy": {"items": [{"type": "model", "id": str(designated), "enabled": True}]}
    }
    await db.commit()
    assert designated not in await _models(db, world.other_ctx.org_id)


async def test_allow_system_models_false_removes_every_platform_model(db: AsyncSession) -> None:
    world, catalog, designated = await _seed(db)
    await _settings(db, world, **{MODEL_ORGS_KEY: {str(designated): [str(world.ctx.org_id)]}})
    tenant = await db.get(Tenant, world.tenant)
    assert tenant is not None
    tenant.limits = {"allow_system_models": False}
    await db.commit()
    assert await _models(db, world.ctx.org_id) == {designated}
    assert await _models(db, world.other_ctx.org_id) == set()


async def test_company_credential_fanout_follows_designation(db: AsyncSession) -> None:
    # Given: a company shared credential designated to the first organization.
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    shared = await credential(db, tenant_id=world.tenant, key="sk-company")
    await _settings(db, world, **{CREDENTIAL_ORGS_KEY: {str(shared.id): [str(world.ctx.org_id)]}})
    grants = service(store, clock)
    # When: both organizations are synced.
    for ctx in (world.ctx, world.other_ctx):
        org = await db.get(Organization, ctx.org_id)
        assert org is not None
        change = await sync_fanout(db, ctx, await organization_fanout(db, org), service=grants)
        await db.commit()
        await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    # Then: only the designated organization's members can use it.
    obj = f"credential:{shared.id}"
    assert can_use(store, world.ctx.org_id, obj)
    assert not can_use(store, world.other_ctx.org_id, obj)
    # And: an organization catalog sync keeps the credential grants (no accidental revoke).
    org = await db.get(Organization, world.ctx.org_id)
    assert org is not None
    again = await sync_fanout(db, world.ctx, await organization_fanout(db, org), service=grants)
    await db.commit()
    assert again.outbox_ids == ()
