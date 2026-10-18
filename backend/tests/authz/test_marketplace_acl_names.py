"""``GET /api/marketplace/items/{id}/acl``: the share dialog's named recipients (S16)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.authz.d04_support import AuthzSwitch, authz, seed_scope
from tests.authz.d05_support import BASE, OTHER_USER_ID, TEST_USER_ID, add_item, regular_user

__all__ = ["authz", "regular_user"]  # fixture re-exports


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


@pytest.mark.usefixtures("regular_user")
async def test_manager_sees_recipient_names_and_others_get_404(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    await db.execute(update(User).where(User.id == OTHER_USER_ID).values(display_name="이하은"))
    await db.commit()
    mine = await add_item(
        db, visibility="restricted", owner=TEST_USER_ID, acl={OTHER_USER_ID: "install"}
    )
    theirs = await add_item(db, visibility="restricted", acl={TEST_USER_ID: "install"})
    authz.use(mode="legacy")
    response = await client.get(f"{BASE}/items/{mine.id}/acl")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"user_id": str(OTHER_USER_ID), "display_name": "이하은", "permission": "install"}
        ]
    }
    # An install-only recipient does not manage the item: no recipient list.
    denied = await client.get(f"{BASE}/items/{theirs.id}/acl")
    assert denied.status_code == 404  # legacy: same answer as the ACL write routes
