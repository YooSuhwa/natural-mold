"""Private catalog ownership and publishing cannot cross organization boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.scoping import private_scoped
from app.exceptions import AppError
from app.marketplace import agent_spec
from app.marketplace import service as catalog
from app.marketplace.schemas import PublishAgentIn
from app.models.marketplace import MarketplaceItem

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_catalog_private_owner_alias_and_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    context = private_scopes.first
    item = MarketplaceItem(
        owner_user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Catalog fixture",
        slug="catalog-fixture",
        resource_type="agent",
    )
    db.add(item)
    await db.commit()
    assert item.user_id == item.owner_user_id
    assert (
        await db.scalar(
            private_scoped(select(MarketplaceItem), MarketplaceItem, private_scopes.second)
        )
        is None
    )


async def test_publish_rejects_foreign_source_before_export(
    db: AsyncSession, collector_case: CollectorCase, org_fixture: OrgFixture
) -> None:
    from app.authz.org_context import resolve_org_context

    context = await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.second))
    assert collector_case.conversation.agent_id is not None
    with pytest.raises(AppError) as captured:
        await agent_spec.publish_agent(
            db,
            context=context,
            agent_id=collector_case.conversation.agent_id,
            user=org_fixture.user,
            body=PublishAgentIn(name="Foreign publish", visibility="private"),
        )
    assert captured.value.status == 404


async def test_catalog_detail_rejects_same_owner_foreign_private_org(
    db: AsyncSession, private_scopes: PrivateScopes, org_fixture: OrgFixture
) -> None:
    context = private_scopes.first
    item = MarketplaceItem(
        owner_user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Private catalog fixture",
        slug="private-detail-fixture",
        resource_type="agent",
        visibility="private",
        status="published",
    )
    db.add(item)
    await db.commit()
    assert (
        await catalog.get_item(
            db, user=org_fixture.user, context=private_scopes.second, item_id=item.id
        )
        is None
    )
