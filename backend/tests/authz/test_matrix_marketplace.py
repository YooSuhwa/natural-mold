"""D-09 matrix: marketplace items, lists, the operator bypass and SUPER moderation (9.6, 10.5)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import add_item
from tests.authz.matrix_support import (
    ACTORS,
    ME,
    MODES,
    Actor,
    Matrix,
    Mode,
    Probe,
    Target,
    assert_contract,
    cases,
    matrix,
    place,
)
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["matrix"]  # fixture re-export
BASE = "/api/marketplace"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _item(visibility: str = "restricted", status: str = "published") -> Target:
    async with TestSession() as db:
        item = await add_item(db, visibility=visibility, owner=TEST_USER_ID, status=status)
    path = {"item_id": str(item.id)}
    return Target(
        "marketplace_items", "marketplace_item", item.id, path, owner_column="owner_user_id"
    )


PROBES = (
    Probe(f"GET {BASE}/items/{{item_id}}"),
    Probe(f"GET {BASE}/items/{{item_id}}/versions"),
    Probe(f"PATCH {BASE}/items/{{item_id}}", {"description": "managed"}),
    Probe(f"POST {BASE}/items/{{item_id}}/disable"),
)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases(PROBES), ids=str)
async def test_item_routes_follow_the_8_4_table(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    await matrix.check(client, probe, actor, mode, await _item())


@pytest.mark.parametrize("route", [f"GET {BASE}/items", f"GET {BASE}/items/page"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("actor", ACTORS)
async def test_item_lists_show_only_confirmed_candidates(
    client: AsyncClient, matrix: Matrix, route: str, actor: Actor, mode: Mode
) -> None:
    await matrix.check_list(client, route, actor, mode, await _item())


@pytest.mark.parametrize("mode", MODES)
async def test_enforced_decisions_never_consult_is_super_user(
    client: AsyncClient, matrix: Matrix, mode: Mode
) -> None:
    target = await _item()
    await place(target, "stranger")
    matrix.as_user(super_user=True)
    matrix.use(mode)  # no tuples: an operator is an ordinary principal under enforce
    response = await matrix.call(client, Probe(f"GET {BASE}/items/{{item_id}}"), target)
    assert_contract(response, {"legacy": 404, "enforce": 404, "down": 503}[mode])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("super_user", [False, True])
async def test_super_listing_toggle_is_operator_only(
    client: AsyncClient, matrix: Matrix, super_user: bool, mode: Mode
) -> None:
    target = await _item("public")
    await place(target, "stranger")
    matrix.as_user(super_user=super_user)
    matrix.use(mode)
    probe = Probe(f"POST {BASE}/admin/items/{{item_id}}/listed", {"is_listed": False})
    response = await matrix.call(client, probe, target)
    assert_contract(response, 200 if super_user else 403)


@pytest.mark.parametrize("manager", [False, True])
async def test_disabled_item_is_hidden_from_viewers_but_not_managers(
    client: AsyncClient, matrix: Matrix, manager: bool
) -> None:
    """The status rule after the permission: a disabled row is the legacy owner's (and so
    an enforced manager's) only; this also keeps the disable read-back from 404ing."""
    target = await _item(status="disabled")
    await place(target, "granted")
    held = ("can_view", "can_manage") if manager else ("can_view",)
    matrix.use("enforce", {(ME, perm, f"marketplace_item:{target.id}") for perm in held})
    response = await matrix.call(client, Probe(f"GET {BASE}/items/{{item_id}}"), target)
    assert_contract(response, 200 if manager else 404)
