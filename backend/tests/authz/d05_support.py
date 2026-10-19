"""Seeds for the D-05 marketplace route conversions (plan 9.6, 10.5)."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user
from app.models.authz import ResourceGrant
from app.models.marketplace import MarketplaceItem, MarketplaceItemACL
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

BASE = "/api/marketplace"


def item_obj(item_id: UUID) -> str:
    return f"marketplace_item:{item_id}"


async def add_item(
    db: AsyncSession,
    *,
    visibility: str = "private",
    owner: UUID | None = OTHER_USER_ID,
    org: UUID | None = TEST_ORG_ID,
    status: str = "published",
    is_system: bool = False,
    is_listed: bool = True,
    acl: dict[UUID, str] | None = None,
) -> MarketplaceItem:
    slug = f"d05-{uuid4().hex[:10]}"
    item = MarketplaceItem(
        id=uuid4(),
        resource_type="skill",
        owner_user_id=None if is_system else owner,
        org_id=None if is_system else org,
        tenant_id=None if is_system else TEST_TENANT_ID,
        is_system=is_system,
        is_listed=is_listed,
        name=slug,
        slug=slug,
        visibility="system" if is_system else visibility,
        status=status,
    )
    db.add(item)
    await db.flush()
    for user_id, permission in (acl or {}).items():
        db.add(MarketplaceItemACL(item_id=item.id, user_id=user_id, permission=permission))
    await db.commit()
    return item


async def grant_set(item_id: UUID) -> set[tuple[str, str, UUID]]:
    """Active ``(relation, principal_type, principal_id)`` grants on the item."""
    async with TestSession() as db:
        rows = await db.scalars(
            select(ResourceGrant).where(
                ResourceGrant.resource_type == "marketplace_item",
                ResourceGrant.resource_id == item_id,
                ResourceGrant.state == "active",
            )
        )
        return {(g.relation, g.principal_type, g.principal_id) for g in rows}


@pytest.fixture
def regular_user(test_app: FastAPI) -> Iterator[None]:
    """The test user without ``is_super_user`` (an ordinary organization member)."""

    async def member() -> CurrentUser:
        return CurrentUser(id=TEST_USER_ID, email="test@test.com", name="T", is_super_user=False)

    previous = test_app.dependency_overrides.get(get_current_user)
    test_app.dependency_overrides[get_current_user] = member
    yield
    if previous is not None:
        test_app.dependency_overrides[get_current_user] = previous


__all__ = ["OTHER_ORG_ID", "OTHER_USER_ID", "TEST_ORG_ID", "TEST_USER_ID"]
