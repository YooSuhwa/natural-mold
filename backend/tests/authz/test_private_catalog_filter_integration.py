"""Actual m83 catalog display queries preserve legitimate staged metadata."""

from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.marketplace import service as catalog
from app.marketplace.schemas import MarketplaceItemListFilters
from app.models.marketplace import MarketplaceItem
from app.models.user import User
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration

type FilterAction = Literal["list", "page"]


@pytest.mark.parametrize("action", ["list", "page"])
async def test_native_catalog_filters_run_after_authorized_metadata_flush(
    monkeypatch: pytest.MonkeyPatch, action: FilterAction
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-filter@example.com",
                "name": "Filter fixture",
                "password": "native-filter-fixture-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None
            actor = CurrentUser(id=user.id, email=user.email, name=user.name)
            context = await resolve_org_context(db, actor, cookie=None)
            item = MarketplaceItem(
                owner_user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                resource_type="agent",
                name="Persisted native display",
                slug="native-display-filter",
                visibility="private",
                status="published",
                is_system=False,
            )
            db.add(item)
            await db.commit()
            current = await catalog.get_item(db, user=actor, context=context, item_id=item.id)
            assert current is item
            name = "Accepted staged native display"
            item.name = name
            filters = MarketplaceItemListFilters(q=name)
            match action:
                case "list":
                    rows = await catalog.list_items(
                        db, user=actor, context=context, filters=filters
                    )
                case "page":
                    page = await catalog.list_items_page(
                        db, user=actor, context=context, filters=filters
                    )
                    rows = page.items
                case unreachable:
                    assert_never(unreachable)
            assert len(rows) == 1 and rows[0].id == item.id and rows[0].name == name
            assert item.name == name
            assert (
                await db.scalar(select(MarketplaceItem.name).where(MarketplaceItem.id == item.id))
                == name
            )
