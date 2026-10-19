"""10.5 mapping (D-05 완료 조건): expected grant set == actual grant set, rerun-identical.

The pure table is checked against hand-written oracles; the route dual-write is checked by
reading ``resource_grants`` after each ACL/visibility route in shadow mode.
"""

from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.marketplace_grants import expected_grants, sync_item_grants
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.marketplace import MarketplaceItem
from app.models.organization import OrganizationMember
from app.models.user import User
from tests.authz.d04_support import AuthzSwitch, authz, seed_scope
from tests.authz.d05_support import (
    BASE,
    OTHER_ORG_ID,
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_item,
    grant_set,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TestSession

__all__ = ["authz"]

U1 = uuid5(NAMESPACE_URL, "moldy/d05-u1")
U2 = uuid5(NAMESPACE_URL, "moldy/d05-u2")
U3 = uuid5(NAMESPACE_URL, "moldy/d05-u3")
ORG = uuid5(NAMESPACE_URL, "moldy/d05-org")
ACL = [(U1, "view"), (U2, "install"), (U3, "manage")]
ORG_INSTALLER = ("installer", "organization", ORG)


@pytest.mark.parametrize(
    ("visibility", "acl", "expected"),
    [
        ("private", [], set()),
        ("private", ACL, set()),  # owner only; a manager grant would imply viewer/installer
        (
            "restricted",
            ACL,
            {("viewer", "user", U1), ("installer", "user", U2), ("manager", "user", U3)},
        ),
        ("restricted", [(U1, "install")], {("installer", "user", U1)}),
        ("public", [], {ORG_INSTALLER}),
        ("public", ACL, {ORG_INSTALLER, ("manager", "user", U3)}),  # C-01 deviation
        ("unlisted", [], {ORG_INSTALLER}),
        ("unlisted", ACL, {ORG_INSTALLER, ("manager", "user", U3)}),
    ],
)
def test_expected_grants_follow_the_10_5_table(visibility: str, acl: list, expected: set) -> None:
    assert expected_grants(visibility, acl, org_id=ORG) == expected


def test_system_items_fan_out_installer_to_every_organization() -> None:
    orgs = [uuid4(), uuid4()]
    found = expected_grants("system", [], org_id=None, fanout_org_ids=orgs)
    assert found == {("installer", "organization", org) for org in orgs}


def test_org_scoped_visibility_without_organization_is_rejected() -> None:
    with pytest.raises(ValueError, match="no organization"):
        expected_grants("public", [], org_id=None)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, monkeypatch) -> None:
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    await seed_scope(db)


def _org_installer() -> tuple[str, str, object]:
    return ("installer", "organization", TEST_ORG_ID)


async def test_shadow_acl_replace_writes_exact_user_grants_and_rerun_is_identical(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(
        db, visibility="restricted", owner=TEST_USER_ID, acl={TEST_USER_ID: "view"}
    )
    authz.use(mode="shadow")
    body = {"user_ids": [str(OTHER_USER_ID)], "permission": "manage"}
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=body)).status_code == 200
    assert await grant_set(item.id) == {("manager", "user", OTHER_USER_ID)}
    async with TestSession() as s:
        rows = await s.scalar(select(func.count()).select_from(ResourceGrant))
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=body)).status_code == 200
    assert await grant_set(item.id) == {("manager", "user", OTHER_USER_ID)}
    async with TestSession() as s:
        assert await s.scalar(select(func.count()).select_from(ResourceGrant)) == rows


async def test_shadow_visibility_changes_track_the_table(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(
        db,
        visibility="restricted",
        owner=TEST_USER_ID,
        acl={OTHER_USER_ID: "install"},
    )
    authz.use(mode="shadow")
    patch = f"{BASE}/items/{item.id}"
    acl = {"user_ids": [str(OTHER_USER_ID)], "permission": "install"}
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=acl)).status_code == 200
    assert await grant_set(item.id) == {("installer", "user", OTHER_USER_ID)}
    assert (await client.patch(patch, json={"visibility": "public"})).status_code == 200
    assert await grant_set(item.id) == {_org_installer()}
    assert (await client.patch(patch, json={"visibility": "unlisted"})).status_code == 200
    assert await grant_set(item.id) == {_org_installer()}
    assert (await client.patch(patch, json={"visibility": "private"})).status_code == 200
    assert await grant_set(item.id) == set()


async def test_shadow_public_manage_acl_becomes_a_manager_grant(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    authz.use(mode="shadow")
    body = {"user_ids": [str(OTHER_USER_ID)], "permission": "manage"}
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=body)).status_code == 200
    assert await grant_set(item.id) == {_org_installer(), ("manager", "user", OTHER_USER_ID)}
    removed = await client.delete(f"{BASE}/items/{item.id}/acl/{OTHER_USER_ID}")
    assert removed.status_code == 204
    assert await grant_set(item.id) == {_org_installer()}


async def test_shadow_acl_user_outside_the_organization_gets_no_grant(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    outsider = uuid4()  # a real user, member of the sibling organization only
    db.add(User(id=outsider, email=f"{outsider.hex[:8]}@t.com", name="O", is_active=True))
    await db.flush()
    db.add(
        OrganizationMember(
            org_id=OTHER_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=outsider, status="active"
        )
    )
    await db.commit()
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    authz.use(mode="shadow")
    body = {"user_ids": [str(outsider)], "permission": "manage"}
    response = await client.post(f"{BASE}/items/{item.id}/acl", json=body)
    assert response.status_code == 200, response.text  # legacy answer stands
    assert await grant_set(item.id) == {_org_installer()}  # no grant for the outsider


async def test_shadow_writes_go_through_the_outbox(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(
        db, visibility="restricted", owner=TEST_USER_ID, acl={TEST_USER_ID: "view"}
    )
    authz.use(mode="shadow")
    body = {"user_ids": [str(OTHER_USER_ID)], "permission": "view"}
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=body)).status_code == 200
    async with TestSession() as s:
        queued = await s.scalar(select(func.count()).select_from(AuthzOutbox))
    assert queued and queued > 0


async def test_legacy_acl_writes_leave_grants_untouched(
    client: AsyncClient, db: AsyncSession
) -> None:
    item = await add_item(
        db, visibility="restricted", owner=TEST_USER_ID, acl={TEST_USER_ID: "view"}
    )
    body = {"user_ids": [str(OTHER_USER_ID)], "permission": "manage"}
    assert (await client.post(f"{BASE}/items/{item.id}/acl", json=body)).status_code == 200
    assert await grant_set(item.id) == set()
    async with TestSession() as s:
        assert await s.scalar(select(func.count()).select_from(AuthzOutbox)) == 0


async def test_shadow_sync_ignores_other_organization_items(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    item = await add_item(db, visibility="public", org=OTHER_ORG_ID, owner=TEST_USER_ID)
    seeded = ResourceGrant(
        org_id=OTHER_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="marketplace_item",
        resource_id=item.id,
        relation="installer",
        principal_type="organization",
        principal_id=OTHER_ORG_ID,
        grant_kind="migration",
    )
    db.add(seeded)
    await db.commit()
    authz.use(mode="shadow")
    response = await client.patch(f"{BASE}/items/{item.id}", json={"visibility": "private"})
    assert response.status_code == 404  # not a management candidate here
    stored = await db.get(MarketplaceItem, item.id)
    assert stored is not None
    stored.visibility = "private"  # even a direct sync from this org must not touch it
    change = await sync_item_grants(db, unit_context(TEST_USER_ID), stored, mode="shadow")
    await db.commit()
    assert change.outbox_ids == ()
    assert await grant_set(item.id) == {("installer", "organization", OTHER_ORG_ID)}


async def test_shadow_sync_revokes_stale_migration_grants(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    """Keys match regardless of grant_kind: an m85 row on a now-private item is revoked."""
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="marketplace_item",
            resource_id=item.id,
            relation="installer",
            principal_type="organization",
            principal_id=TEST_ORG_ID,
            grant_kind="migration",
        )
    )
    await db.commit()
    authz.use(mode="shadow")
    patch = f"{BASE}/items/{item.id}"
    assert (await client.patch(patch, json={"visibility": "private"})).status_code == 200
    assert await grant_set(item.id) == set()
