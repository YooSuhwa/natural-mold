"""E-03 review fixes: lock order, org-scoped fanout locks, hide/delete revocation, masking."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import catalog_fanout
from app.authz.catalog_fanout import organization_fanout, sync_fanout
from app.authz.catalog_fanout_backfill import backfill_catalog_fanout
from app.authz.catalog_item_fanout import sync_item_fanout
from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantService
from app.authz.outbox import apply_now, process_outbox
from app.authz.service import AuthzService
from app.models.authz import ResourceGrant
from app.models.mcp_server import McpServer
from app.models.organization import Organization
from tests.authz.d04_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    AuthzSwitch,
    OwnerStore,
    allow,
    authz,
    seed_scope,
)
from tests.authz.e03_support import can_use, physical_model_scope, seed_catalog
from tests.authz.grant_testkit import Clock, seed_world, service
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["authz", "physical_model_scope"]  # fixture re-exports


class Recording(GrantService):
    def __init__(self, log: list[str], base: GrantService) -> None:
        super().__init__(base._writer, base._sessions, clock=base._clock)
        self._log = log

    async def grant(self, db, ctx, target, request):
        self._log.append(f"grant {target.resource_type}:{target.resource_id}")
        return await super().grant(db, ctx, target, request)

    async def revoke(self, db, ctx, grant_id):
        self._log.append("revoke")
        return await super().revoke(db, ctx, grant_id)


async def _sync(db: AsyncSession, ctx, grants: GrantService):
    org = await db.get(Organization, ctx.org_id)
    assert org is not None
    change = await sync_fanout(db, ctx, await organization_fanout(db, org), service=grants)
    await db.commit()
    return org, change


async def test_sync_locks_every_touched_object_in_sorted_order_before_any_change(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    await seed_catalog(db)
    log: list[str] = []
    real = catalog_fanout.lock_object

    async def recording_lock(db, resource_type, resource_id, *, org_id=None):
        assert org_id == world.ctx.org_id  # fanout keys are per organization
        log.append(f"lock {resource_type}:{resource_id}")
        await real(db, resource_type, resource_id, org_id=org_id)

    monkeypatch.setattr(catalog_fanout, "lock_object", recording_lock)
    await _sync(db, world.ctx, Recording(log, service(store, clock)))
    locks = [entry for entry in log if entry.startswith("lock")]
    assert locks == sorted(locks) and len(locks) == 4  # tool, server, two visible models
    assert log[: len(locks)] == locks  # every lock precedes the first grant/revoke


async def test_one_org_revoking_keeps_the_other_orgs_fanout_tuples(db: AsyncSession) -> None:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    catalog = await seed_catalog(db)
    grants = service(store, clock)
    for ctx in (world.ctx, world.other_ctx):
        _, change = await _sync(db, ctx, grants)
        await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    org = await db.get(Organization, world.ctx.org_id)
    assert org is not None
    org.settings = {
        "catalog_policy": {"items": [{"type": "tool", "id": str(catalog.tool), "enabled": False}]}
    }
    await db.flush()
    _, change = await _sync(db, world.ctx, grants)
    await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    assert not can_use(store, world.ctx.org_id, f"tool:{catalog.tool}")
    assert can_use(store, world.other_ctx.org_id, f"tool:{catalog.tool}")


async def test_disabled_system_mcp_server_loses_its_fanout_everywhere(db: AsyncSession) -> None:
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    catalog = await seed_catalog(db)
    await backfill_catalog_fanout(TestSession, world.ctx.user_id, tenant_id=world.tenant)
    await process_outbox(TestSession, store, now=clock.tick())
    server = await db.get(McpServer, catalog.server)
    assert server is not None
    server.status = "disabled"
    change = await sync_item_fanout(
        db, "mcp_server", catalog.server, world.ctx.user_id, service=service(store, clock)
    )
    await db.commit()
    await apply_now(TestSession, store, change.outbox_ids, now=clock.tick())
    for ctx in (world.ctx, world.other_ctx):
        assert not can_use(store, ctx.org_id, f"mcp_server:{catalog.server}")
        assert can_use(store, ctx.org_id, f"tool:{catalog.tool}")


async def _model_state(db: AsyncSession, model: UUID) -> set[str]:
    found = await db.scalars(select(ResourceGrant.state).where(ResourceGrant.resource_id == model))
    return set(found)


async def test_hiding_or_deleting_a_model_revokes_its_fanout_and_showing_follows_policy(
    db: AsyncSession, client: AsyncClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two organizations fanned out; the sibling organization turned the model off.
    monkeypatch.setattr("app.database.async_session", TestSession)
    store = OwnerStore()
    monkeypatch.setattr(test_app.state, "authz", AuthzService(store, mode="enforce"), False)
    await seed_scope(db)
    catalog = await seed_catalog(db)
    sibling = await db.get(Organization, OTHER_ORG_ID)
    assert sibling is not None
    sibling.settings = {
        "catalog_policy": {"items": [{"type": "model", "id": str(catalog.model), "enabled": False}]}
    }
    await db.commit()
    await backfill_catalog_fanout(TestSession, TEST_USER_ID)
    await process_outbox(TestSession, store)
    model = f"llm_model:{catalog.model}"
    assert can_use(store, TEST_ORG_ID, model) and not can_use(store, OTHER_ORG_ID, model)
    path = f"/api/models/{catalog.model}"
    # When/Then: hiding revokes everywhere before the response.
    assert (await client.patch(path, json={"is_visible": False})).status_code == 200
    assert not can_use(store, TEST_ORG_ID, model)
    assert "active" not in await _model_state(db, catalog.model)
    # Showing again re-grants only where the policies allow it.
    assert (await client.patch(path, json={"is_visible": True})).status_code == 200
    assert can_use(store, TEST_ORG_ID, model) and not can_use(store, OTHER_ORG_ID, model)
    # Deleting retires the fanout as well.
    assert (await client.delete(path)).status_code == 204
    assert not can_use(store, TEST_ORG_ID, model)
    assert "active" not in await _model_state(db, catalog.model)


async def test_system_mcp_url_args_and_export_label_are_masked_for_members(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    await seed_scope(db)
    system = McpServer(
        user_id=OTHER_USER_ID,
        is_system=True,
        name="sys",
        transport="streamable_http",
        url="https://op:pw@mcp.example.test:8443/x?token=abc&mode=1",
        args=["--key", "secret"],
    )
    db.add(system)
    await db.commit()
    obj = f"mcp_server:{system.id}"
    authz.use(allow(("can_use", obj), ("can_view", obj)))
    detail = (await client.get(f"/api/mcp-servers/{system.id}")).json()
    assert (
        detail["url"] == "https://mcp.example.test:8443/x?token=%3Credacted%3E&mode=%3Credacted%3E"
    )
    assert detail["args"] == ["<redacted>", "<redacted>"]
    exported = (await client.get("/api/mcp-servers/export")).json()["mcpServers"]
    assert list(exported) == ["sys (system)"]
    assert exported["sys (system)"]["args"] == ["<redacted>", "<redacted>"]
    assert "abc" not in exported["sys (system)"]["url"]
