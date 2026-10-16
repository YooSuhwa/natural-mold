"""E-03 catalog-policy routes: PUT fans out (enforce) or stays storage-only (legacy); impact."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout_backfill import backfill_catalog_fanout
from app.authz.outbox import process_outbox
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.organization import OrganizationMember
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OwnerStore
from tests.authz.e03_support import Catalog, can_use, physical_model_scope, seed_catalog
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import TestSession, make_user

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]
__all__ = ["physical_model_scope"]  # autouse fixture re-export


@pytest.fixture
def store(raw_test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[OwnerStore]:
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    fake = OwnerStore()
    service = AuthzService(fake, mode="enforce")

    async def override() -> AuthzService:
        return service

    raw_test_app.dependency_overrides[get_authz] = override
    yield fake
    raw_test_app.dependency_overrides.pop(get_authz, None)


async def _put(client: AsyncClient, fixture: OrgFixture, items: list[dict[str, object]]):
    path = f"/api/orgs/{fixture.first}/catalog-policy"
    current = await client.get(path)
    headers = {**fixture.session.headers(), "If-Match": current.headers["etag"]}
    return await client.put(path, json={"items": items}, headers=headers)


async def _seed(db: AsyncSession, fixture: OrgFixture) -> Catalog:
    catalog = await seed_catalog(db)
    await grant_admin(db, fixture)
    other = await make_user(db)
    db.add(OrganizationMember(org_id=fixture.first, tenant_id=fixture.tenant, user_id=other.id))
    await db.commit()
    return catalog


async def test_enforced_put_turns_can_use_off_and_back_on(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture, store: OwnerStore
) -> None:
    # Given: an organization whose catalog was backfilled into OpenFGA.
    catalog = await _seed(db, org_fixture)
    await backfill_catalog_fanout(TestSession, org_fixture.user.id, org_id=org_fixture.first)
    await process_outbox(TestSession, store)
    tool, model = f"tool:{catalog.tool}", f"llm_model:{catalog.model}"
    assert can_use(store, org_fixture.first, tool) and can_use(store, org_fixture.first, model)
    # When: the organization admin turns the tool and the model off.
    off = [
        {"type": "tool", "id": str(catalog.tool), "enabled": False},
        {"type": "model", "id": str(catalog.model), "enabled": False},
    ]
    changed = await _put(raw_client, org_fixture, off)
    # Then: the deletes landed before the response; no member keeps can_use.
    assert changed.status_code == 200, changed.text
    assert not can_use(store, org_fixture.first, tool)
    assert not can_use(store, org_fixture.first, model)
    assert can_use(store, org_fixture.first, f"mcp_server:{catalog.server}")
    revoked = await db.scalars(
        select(ResourceGrant.state).where(ResourceGrant.resource_id == catalog.tool)
    )
    assert set(revoked) == {"revoked"}
    # The returned ETag is the stored version: a follow-up edit is not a stale write.
    headers = {**org_fixture.session.headers(), "If-Match": changed.headers["etag"]}
    path = f"/api/orgs/{org_fixture.first}/catalog-policy"
    restored = await raw_client.put(path, json={"items": []}, headers=headers)
    assert restored.status_code == 200, restored.text
    assert can_use(store, org_fixture.first, tool) and can_use(store, org_fixture.first, model)


async def test_legacy_put_stores_policy_without_fanout_writes(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    catalog = await _seed(db, org_fixture)
    changed = await _put(
        raw_client, org_fixture, [{"type": "tool", "id": str(catalog.tool), "enabled": False}]
    )
    assert changed.status_code == 200
    assert await db.scalar(select(func.count()).select_from(ResourceGrant)) == 0
    assert await db.scalar(select(func.count()).select_from(AuthzOutbox)) == 0


async def test_impact_counts_live_agents_and_hides_unknown_items(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    catalog = await _seed(db, org_fixture)
    agents = [
        Agent(name=name, system_prompt="p", user_id=org_fixture.user.id, model_id=catalog.model)
        for name in ("live", "archived")
    ]
    for agent in agents:
        agent.org_id, agent.tenant_id = org_fixture.first, org_fixture.tenant
    agents[1].archived_at = func.now()
    db.add_all(agents)
    await db.flush()
    db.add_all(AgentToolLink(agent_id=a.id, tool_id=catalog.tool) for a in agents)
    await db.commit()
    path = f"/api/orgs/{org_fixture.first}/catalog-policy/impact"
    tool = await raw_client.get(path, params={"type": "tool", "id": str(catalog.tool)})
    assert tool.status_code == 200, tool.text
    assert (tool.json()["affected_agents"], tool.json()["required_agents"]) == (1, 0)
    model = await raw_client.get(path, params={"type": "model", "id": str(catalog.model)})
    assert (model.json()["affected_agents"], model.json()["required_agents"]) == (1, 1)
    missing = await raw_client.get(path, params={"type": "tool", "id": str(catalog.model)})
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
