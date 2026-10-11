"""Catalog responses use current persisted version associations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.marketplace.service import get_version
from app.models.marketplace import MarketplaceItem, MarketplaceVersion

if TYPE_CHECKING:
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]


@pytest.mark.parametrize("moved", [False, True])
async def test_catalog_latest_version_belongs_to_requested_item(
    db: AsyncSession, agent_case: AgentCase, moved: bool
) -> None:
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    publication = await agent_case.client.post(
        f"/api/marketplace/items/from-agent/{agent_case.agent_id}",
        json={"name": "Catalog association fixture", "visibility": "private"},
        headers={"X-CSRF-Token": csrf},
    )
    assert publication.status_code == 201, publication.text
    item_id = UUID(publication.json()["id"])
    item = await db.get(MarketplaceItem, item_id)
    assert item is not None and item.latest_version_id is not None
    version_id = item.latest_version_id
    if moved:
        target = MarketplaceItem(
            owner_user_id=item.owner_user_id,
            org_id=item.org_id,
            tenant_id=item.tenant_id,
            resource_type="agent",
            name="Different catalog fixture",
            slug=f"fixture-{uuid4().hex}",
            visibility="private",
            status="published",
            is_system=False,
        )
        db.add(target)
        await db.flush()
        await db.execute(
            update(MarketplaceVersion)
            .where(MarketplaceVersion.id == version_id)
            .values(item_id=target.id, execution_profile={"support_level": "fixture_marker"})
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    response = await agent_case.client.get(f"/api/marketplace/items/{item_id}")
    assert response.status_code == 200, response.text
    if moved:
        assert response.json()["latest_version"] is None, response.text
        assert response.json()["execution_profile"] is None, response.text
    else:
        assert response.json()["latest_version"]["id"] == str(version_id)


async def test_version_detail_refreshes_persisted_parent_and_payload(
    db: AsyncSession, agent_case: AgentCase
) -> None:
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    publication = await agent_case.client.post(
        f"/api/marketplace/items/from-agent/{agent_case.agent_id}",
        json={"name": "Detail association fixture", "visibility": "private"},
        headers={"X-CSRF-Token": csrf},
    )
    assert publication.status_code == 201, publication.text
    source = await db.get(MarketplaceItem, UUID(publication.json()["id"]))
    assert source is not None and source.latest_version_id is not None
    cached = await db.get(MarketplaceVersion, source.latest_version_id)
    assert cached is not None
    target = MarketplaceItem(
        owner_user_id=source.owner_user_id,
        org_id=source.org_id,
        tenant_id=source.tenant_id,
        resource_type="agent",
        name="Current detail fixture",
        slug=f"fixture-{uuid4().hex}",
        visibility="private",
        status="published",
        is_system=False,
    )
    db.add(target)
    await db.flush()
    await db.execute(
        update(MarketplaceVersion)
        .where(MarketplaceVersion.id == cached.id)
        .values(item_id=target.id, payload={"name": "Current detail association"})
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert cached.item_id == source.id
    assert (
        source.owner_user_id is not None
        and source.org_id is not None
        and source.tenant_id is not None
    )
    current = await get_version(
        db,
        user=CurrentUser(id=source.owner_user_id, email="fixture@example.com", name="Fixture"),
        context=OrgContext(
            user_id=source.owner_user_id,
            org_id=source.org_id,
            tenant_id=source.tenant_id,
            is_super_user=False,
        ),
        version_id=cached.id,
    )
    assert current is not None and current.item_id == target.id
    assert current.payload == {"name": "Current detail association"}
    response = await agent_case.client.get(f"/api/marketplace/versions/{cached.id}")
    assert response.status_code == 200, response.text
    assert response.json()["item_id"] == str(target.id), response.text
    assert response.json()["payload"] == {"name": "Current detail association"}
