"""H-05: ``my_permissions`` for marketplace items (plan 13.1, 13.9; D-05 vocabulary)."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.authz.d04_support import (
    AuthzSwitch,
    Unavailable,
    allow,
    authz,
    grant_to_me,
    seed_scope,
)
from tests.authz.d05_support import OTHER_ORG_ID, TEST_USER_ID, add_item, item_obj, regular_user

__all__ = ["authz", "regular_user"]

ALL = ["can_view", "can_install", "can_manage"]
PATH = "/api/resources/marketplace_item/permissions"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _answer(client: AsyncClient, *ids: object) -> dict[str, list[str]]:
    response = await client.get(PATH, params=[("ids", str(i)) for i in ids])
    assert response.status_code == 200, response.text
    return {item["id"]: item["my_permissions"] for item in response.json()["items"]}


async def test_legacy_owner_manages_viewers_install_and_hidden_ids_are_omitted(
    client: AsyncClient, db: AsyncSession, regular_user: None
) -> None:
    mine = await add_item(db, owner=TEST_USER_ID, status="draft")
    public = await add_item(db, visibility="public")
    acl_install = await add_item(db, visibility="restricted", acl={TEST_USER_ID: "install"})
    acl_view = await add_item(db, visibility="restricted", acl={TEST_USER_ID: "view"})
    restricted = await add_item(db, visibility="restricted")
    private = await add_item(db, visibility="private")
    disabled = await add_item(db, visibility="public", status="disabled")
    foreign = await add_item(db, visibility="private", org=OTHER_ORG_ID)
    system = await add_item(db, is_system=True)
    ids = [mine, public, acl_install, acl_view, restricted, private, disabled, foreign, system]
    answer = await _answer(client, *(i.id for i in ids), uuid4())
    assert answer == {
        str(mine.id): ALL,
        str(public.id): ["can_view", "can_install"],
        str(acl_install.id): ["can_view", "can_install"],
        str(acl_view.id): ["can_view"],
        str(system.id): ["can_view", "can_install"],
    }


async def test_legacy_operator_manages_visible_items(client: AsyncClient, db: AsyncSession) -> None:
    public = await add_item(db, visibility="public")
    assert await _answer(client, public.id) == {str(public.id): ALL}


async def test_enforce_answers_held_permissions_of_confirmed_candidates_only(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    installer = await add_item(db, visibility="restricted")
    viewer = await add_item(db, visibility="restricted")
    ungranted = await add_item(db, visibility="restricted")
    unconfirmed = await add_item(db, visibility="restricted")
    for item in (installer, viewer, unconfirmed):
        await grant_to_me(db, "marketplace_item", item.id)
    authz.use(
        allow(
            ("can_view", item_obj(installer.id)),
            ("can_install", item_obj(installer.id)),
            ("can_view", item_obj(viewer.id)),
            ("can_view", item_obj(ungranted.id)),
            ("can_manage", item_obj(unconfirmed.id)),
        )
    )
    answer = await _answer(client, installer.id, viewer.id, ungranted.id, unconfirmed.id)
    assert answer == {
        str(installer.id): ["can_view", "can_install"],
        str(viewer.id): ["can_view"],
    }


async def test_enforce_never_offers_install_of_a_disabled_item(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    mine = await add_item(db, owner=TEST_USER_ID, status="disabled")
    authz.use(allow(*((perm, item_obj(mine.id)) for perm in ALL)))
    assert await _answer(client, mine.id) == {str(mine.id): ["can_view", "can_manage"]}


async def test_enforce_ignores_the_operator_flag(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    public = await add_item(db, visibility="public")
    authz.use()
    assert await _answer(client, public.id) == {}


async def test_enforce_outage_is_503_authz_unavailable(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    mine = await add_item(db, owner=TEST_USER_ID)
    authz.use(client=Unavailable())
    response = await client.get(PATH, params={"ids": str(mine.id)})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "authz_unavailable")


async def test_shadow_keeps_the_legacy_answer_and_samples_the_first_rows(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    items = [await add_item(db, visibility="public") for _ in range(5)]
    authz.use(mode="shadow")
    answer = await _answer(client, *(i.id for i in items))
    assert answer == {str(i.id): ["can_view", "can_install"] for i in items}
    assert len(authz.samples) == 3 + 3 * len(ALL)


async def test_id_count_is_capped(client: AsyncClient) -> None:
    response = await client.get(PATH, params=[("ids", str(uuid4())) for _ in range(101)])
    assert response.status_code == 422


@pytest.mark.parametrize("mode", ["legacy", "enforce"])
async def test_a_deactivated_actor_gets_the_catalog_denial(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, mode: str
) -> None:
    mine = await add_item(db, owner=TEST_USER_ID)
    if mode == "enforce":
        authz.use(allow(("can_view", item_obj(mine.id))))
    await db.execute(update(User).where(User.id == TEST_USER_ID).values(is_active=False))
    await db.commit()
    catalog = await client.get("/api/marketplace/items")
    answer = await client.get(PATH, params={"ids": str(mine.id)})
    assert catalog.status_code == answer.status_code == 404
    assert answer.json()["error"]["code"] == catalog.json()["error"]["code"]
