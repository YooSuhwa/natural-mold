"""D-05 marketplace routes (plan 9.6, 8.4): enforce, shadow and the operator bypass.

Legacy byte-identity is covered by the existing ``tests/test_marketplace_*.py`` suites
(they run with ``AUTHZ_MODE=legacy`` and are unchanged apart from a static regex).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.service_types import Verdict
from tests.authz.d04_support import AuthzSwitch, Unavailable, allow, authz, seed_scope
from tests.authz.d05_support import (
    BASE,
    OTHER_ORG_ID,
    TEST_ORG_ID,
    add_item,
    item_obj,
    regular_user,
)

__all__ = ["authz", "regular_user"]  # fixture re-exports

CAPS = allow(("cap_create_skill", f"organization:{TEST_ORG_ID}"))


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


def _item_routes(item_id: object) -> list[tuple[str, str, dict | None]]:
    """Every item-scoped route (method, path, body) of the 21 in 9.6."""
    return [
        ("GET", f"{BASE}/items/{item_id}", None),
        ("GET", f"{BASE}/items/{item_id}/versions", None),
        ("POST", f"{BASE}/items/{item_id}/install", {}),
        ("PATCH", f"{BASE}/items/{item_id}", {"description": "x"}),
        ("POST", f"{BASE}/items/{item_id}/acl", {"user_ids": [], "permission": "install"}),
        ("DELETE", f"{BASE}/items/{item_id}/acl/{item_id}", None),
        ("POST", f"{BASE}/items/{item_id}/disable", None),
        ("POST", f"{BASE}/items/{item_id}/enable", None),
    ]


async def _send(client: AsyncClient, method: str, path: str, body: dict | None):
    return await client.request(method, path, json=body)


@pytest.mark.parametrize("index", range(8))
async def test_enforced_operator_without_tuples_gets_404_on_item_routes(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, index: int
) -> None:
    item = await add_item(db, visibility="private")
    authz.use(CAPS)  # org capabilities held: only the item decision is under test
    method, path, body = _item_routes(item.id)[index]
    response = await _send(client, method, path, body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"


@pytest.mark.parametrize("index", range(8))
async def test_enforced_member_without_tuples_gets_404_on_item_routes(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None, index: int
) -> None:
    item = await add_item(db, visibility="public")
    authz.use(CAPS)
    method, path, body = _item_routes(item.id)[index]
    assert (await _send(client, method, path, body)).status_code == 404


async def test_enforced_operator_list_hides_ungranted_items(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    hidden = await add_item(db, visibility="private")
    shown = await add_item(db, visibility="restricted")
    from tests.authz.d04_support import grant_to_me

    await grant_to_me(db, "marketplace_item", shown.id)
    authz.use(allow(("can_view", item_obj(shown.id))))
    for path in (f"{BASE}/items", f"{BASE}/items/page"):
        body = (await client.get(path)).json()
        rows = body["items"] if isinstance(body, dict) else body
        ids = {row["id"] for row in rows}
        assert str(shown.id) in ids and str(hidden.id) not in ids


async def test_enforced_view_allows_detail_and_versions(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="restricted")
    authz.use(allow(("can_view", item_obj(item.id))))
    assert (await client.get(f"{BASE}/items/{item.id}")).status_code == 200
    assert (await client.get(f"{BASE}/items/{item.id}/versions")).status_code == 200


async def test_enforced_viewer_without_manage_gets_403(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="restricted")
    authz.use(allow(("can_view", item_obj(item.id))) | CAPS)
    response = await client.patch(f"{BASE}/items/{item.id}", json={"description": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_required"
    install = await client.post(f"{BASE}/items/{item.id}/install", json={})
    assert install.status_code == 403


async def test_enforced_manager_grant_can_patch_non_owned_item(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="restricted")
    obj = item_obj(item.id)
    authz.use(allow(("can_view", obj), ("can_manage", obj)))
    response = await client.patch(f"{BASE}/items/{item.id}", json={"description": "managed"})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "managed"


async def test_enforced_other_org_item_is_404_even_when_allowed(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", org=OTHER_ORG_ID)
    authz.use(allow(("can_view", item_obj(item.id))))
    assert (await client.get(f"{BASE}/items/{item.id}")).status_code == 404


async def test_enforced_disabled_item_stays_hidden_from_viewers(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="public", status="disabled")
    authz.use(allow(("can_view", item_obj(item.id)), ("can_install", item_obj(item.id))))
    assert (await client.get(f"{BASE}/items/{item.id}")).status_code == 404


async def test_enforced_unavailable_is_503(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public")
    authz.use(client=Unavailable())
    response = await client.get(f"{BASE}/items/{item.id}")
    assert response.status_code == 503


async def test_enforced_operator_moderates_platform_items_only(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    system = await add_item(db, is_system=True)
    org_item = await add_item(db, visibility="public")
    authz.use()
    assert (await client.post(f"{BASE}/items/{system.id}/disable")).status_code == 200
    assert (await client.post(f"{BASE}/items/{system.id}/enable")).status_code == 200
    assert (await client.post(f"{BASE}/items/{org_item.id}/disable")).status_code == 404


async def test_enforced_member_cannot_moderate_platform_items(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    system = await add_item(db, is_system=True)
    authz.use()
    assert (await client.post(f"{BASE}/items/{system.id}/disable")).status_code == 404


async def test_admin_routes_are_super_only_in_enforce(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", org=OTHER_ORG_ID)
    authz.use()
    listed = await client.post(f"{BASE}/admin/items/{item.id}/listed", json={"is_listed": False})
    assert listed.status_code == 200, listed.text
    assert (await client.post(f"{BASE}/admin/k-skill/sync")).status_code == 200


async def test_admin_routes_reject_members(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="public")
    authz.use()
    listed = await client.post(f"{BASE}/admin/items/{item.id}/listed", json={"is_listed": False})
    assert listed.status_code == 403
    assert (await client.post(f"{BASE}/admin/k-skill/sync")).status_code == 403


async def test_shadow_answers_legacy_and_samples(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    visible = await add_item(db, visibility="public")
    hidden = await add_item(db, visibility="private")
    authz.use(mode="shadow")
    assert (await client.get(f"{BASE}/items/{visible.id}")).status_code == 200
    assert (await client.get(f"{BASE}/items/{hidden.id}")).status_code == 404
    # Not a management candidate (other owner): the legacy 404 stands.
    patched = await client.patch(f"{BASE}/items/{visible.id}", json={"description": "x"})
    assert patched.status_code == 404
    decided = {(s.obj, s.permission): s.legacy for s in authz.samples}
    assert decided[(item_obj(visible.id), "can_view")] is Verdict.ALLOW
    assert decided[(item_obj(hidden.id), "can_view")] is Verdict.NOT_FOUND
    assert decided[(item_obj(visible.id), "can_manage")] is Verdict.NOT_FOUND
    assert all(s.resource_org_id == TEST_ORG_ID for s in authz.samples)
