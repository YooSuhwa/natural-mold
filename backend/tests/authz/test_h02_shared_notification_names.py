"""H-02: a ``grant.received`` row keeps the sharer and item names while the share lasts."""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target
from app.models.authz import ResourceGrant
from tests.authz.d04_support import OTHER_USER_ID, AuthzSwitch, add_tool, authz, grant_to_me
from tests.authz.e01_support import seed
from tests.authz.h02_support import PATH, push

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def test_legacy_grant_received_names_the_item_and_sharer_while_shared(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID, name="영업 리포트")
    await grant_to_me(db, "tool", tool.id)
    await push(db, target=Target("tool", tool.id, tool.name), actor=OTHER_USER_ID, level="viewer")
    await push(
        db, kind="trigger.blocked", target=Target("tool", tool.id, tool.name), actor=OTHER_USER_ID
    )
    authz.use(mode="legacy")  # legacy: only the owner opens the tool
    items = {item["kind"]: item for item in (await client.get(PATH)).json()["items"]}
    shared = items["grant.received"]
    assert shared["target"] == {
        "type": "tool",
        "id": str(tool.id),
        "name": "영업 리포트",
        "accessible": True,
    }
    assert shared["actor"]["id"] == str(OTHER_USER_ID)
    assert shared["link"] is None  # the page itself stays closed in legacy mode
    assert items["trigger.blocked"]["target"]["accessible"] is False  # other kinds unchanged

    await db.execute(update(ResourceGrant).values(state="revoked"))
    await db.commit()
    after = {item["kind"]: item for item in (await client.get(PATH)).json()["items"]}
    assert after["grant.received"]["target"]["accessible"] is False
    assert after["grant.received"]["actor"] is None
