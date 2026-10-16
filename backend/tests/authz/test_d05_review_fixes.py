"""D-05 review fixes: full 21-route matrix, install/publish under enforce, E-09 candidates,
capability oracle, moderation audit, legacy 500 -> 403, schema guard."""

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import CheckQuery, Consistency
from app.models.audit_event import AuditEvent
from app.models.marketplace import MarketplaceItem
from app.models.skill import Skill
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation
from tests.authz.d04_support import AuthzSwitch, OwnerStore, allow, authz, seed_scope
from tests.authz.d05_support import (
    BASE,
    OTHER_ORG_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_item,
    item_obj,
    regular_user,
)
from tests.authz.unit_scope_fixture import create_test_text_skill, unit_context
from tests.conftest import TestSession

__all__ = ["authz", "regular_user"]

ORG = f"organization:{TEST_ORG_ID}"
CAPS = allow(("cap_create_skill", ORG), ("cap_publish_marketplace", ORG), ("cap_register_mcp", ORG))


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, monkeypatch) -> None:
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    await seed_scope(db)


def _routes(item: UUID, missing: UUID) -> list[tuple[str, str, dict | None, set[int]]]:
    """All 21 routes of 9.6: (method, path, body, statuses allowed without tuples)."""
    publish = {"visibility": "public", "name": "n"}
    return [
        ("GET", f"{BASE}/items", None, {200}),
        ("GET", f"{BASE}/items/page", None, {200}),
        ("GET", f"{BASE}/items/{item}", None, {404}),
        ("GET", f"{BASE}/items/{item}/versions", None, {404}),
        ("GET", f"{BASE}/versions/{missing}", None, {404}),
        ("POST", f"{BASE}/items/{item}/install", {}, {404}),
        ("POST", f"{BASE}/installations/{missing}/update", {"strategy": "keep_current"}, {404}),
        ("DELETE", f"{BASE}/installations/{missing}", None, {404}),
        ("POST", f"{BASE}/items/from-skill/{missing}", publish, {404}),
        ("POST", f"{BASE}/items/from-mcp/{missing}", publish, {404}),
        ("POST", f"{BASE}/items/from-agent/{missing}", publish, {404}),
        ("POST", f"{BASE}/items/{item}/versions/from-skill/{missing}", {}, {404}),
        ("POST", f"{BASE}/items/{item}/versions/from-agent/{missing}", {}, {404}),
        ("POST", f"{BASE}/items/{item}/versions/from-mcp/{missing}", {}, {404}),
        ("PATCH", f"{BASE}/items/{item}", {"description": "x"}, {404}),
        ("POST", f"{BASE}/items/{item}/acl", {"user_ids": [], "permission": "install"}, {404}),
        ("DELETE", f"{BASE}/items/{item}/acl/{missing}", None, {404}),
        ("POST", f"{BASE}/items/{item}/disable", None, {404}),
        ("POST", f"{BASE}/items/{item}/enable", None, {404}),
        ("POST", f"{BASE}/admin/items/{missing}/listed", {"is_listed": True}, {404}),
        ("POST", f"{BASE}/admin/k-skill/sync", None, {200}),
    ]


@pytest.mark.parametrize("index", range(21))
async def test_enforced_operator_matrix_all_21_routes(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, index: int
) -> None:
    item = await add_item(db, visibility="private")
    authz.use(CAPS)
    method, path, body, expected = _routes(item.id, uuid4())[index]
    response = await client.request(method, path, json=body)
    assert response.status_code in expected, (path, response.text)
    if response.status_code == 200 and "items" in path and method == "GET":
        payload = response.json()
        rows = payload["items"] if isinstance(payload, dict) else payload
        assert str(item.id) not in {row["id"] for row in rows}


@pytest.mark.parametrize("index", range(21))
async def test_enforced_member_matrix_all_21_routes(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None, index: int
) -> None:
    item = await add_item(db, visibility="public")
    authz.use(CAPS)
    method, path, body, expected = _routes(item.id, uuid4())[index]
    if "/admin/" in path:
        expected = {403}  # SUPER routes
    response = await client.request(method, path, json=body)
    assert response.status_code in expected, (path, response.text)


async def test_enforced_viewer_gets_403_on_every_manage_route(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="restricted")
    authz.use(allow(("can_view", item_obj(item.id))) | CAPS)
    for method, path, body, _ in _routes(item.id, item.id)[11:19]:
        if "/versions/" in path:
            continue  # the source lookup (404) runs before the item manage gate
        response = await client.request(method, path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "permission_required"


async def test_install_cap_never_distinguishes_hidden_from_missing(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    hidden = await add_item(db, visibility="public", org=OTHER_ORG_ID)  # same tenant, other org
    authz.use()  # no capability, no tuples
    for target in (hidden.id, uuid4()):
        response = await client.post(f"{BASE}/items/{target}/install", json={})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"


async def test_install_without_cap_is_403_capability_required(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    item = await add_item(db, visibility="public")
    obj = item_obj(item.id)
    authz.use(allow(("can_view", obj), ("can_install", obj)))
    response = await client.post(f"{BASE}/items/{item.id}/install", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


async def test_company_public_approved_is_visible_and_unapproved_hidden(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, regular_user: None
) -> None:
    approved = await add_item(db, visibility="public", org=OTHER_ORG_ID)
    unapproved = await add_item(db, visibility="public", org=OTHER_ORG_ID)
    stored = await db.get(MarketplaceItem, approved.id)
    assert stored is not None
    stored.tenant_publication = "approved"
    await db.commit()
    authz.use(allow(("can_view", item_obj(approved.id)), ("can_view", item_obj(unapproved.id))))
    assert (await client.get(f"{BASE}/items/{approved.id}")).status_code == 200
    assert (await client.get(f"{BASE}/items/{unapproved.id}")).status_code == 404
    listed = {row["id"] for row in (await client.get(f"{BASE}/items")).json()}
    assert str(approved.id) in listed and str(unapproved.id) not in listed


async def _skill(db: AsyncSession, tmp_path: Path) -> UUID:
    md = '---\nname: d05\ndescription: "d"\nversion: "1.0.0"\n---\n\n# Body\n'
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await create_test_text_skill(
            db,
            TextSkillCreation(
                name=f"d05-{uuid4().hex[:6]}", slug=None, description="d", content=md
            ),
            unit_context(TEST_USER_ID),
        )
    await db.commit()
    return skill.id


async def test_publish_without_cap_is_403_and_without_source_share_is_denied(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, tmp_path: Path
) -> None:
    skill_id = await _skill(db, tmp_path)
    body = {"visibility": "public", "name": "n"}
    authz.use()
    no_cap = await client.post(f"{BASE}/items/from-skill/{skill_id}", json=body)
    assert no_cap.status_code == 403
    assert no_cap.json()["error"]["code"] == "capability_required"
    authz.use(CAPS | allow(("can_use", f"skill:{skill_id}")))
    no_share = await client.post(f"{BASE}/items/from-skill/{skill_id}", json=body)
    assert no_share.status_code == 403
    assert no_share.json()["error"]["code"] == "permission_required"


class InstallStore(OwnerStore):
    """Owner answers (OwnerStore) plus the source item's view/install for the test user."""

    def __init__(self, item: str) -> None:
        super().__init__()
        self.item = item

    def _allowed(self, query: CheckQuery) -> bool:
        if query.object == self.item and query.relation in ("can_view", "can_install"):
            return True
        return super()._allowed(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        return tuple(self._allowed(query) for query in queries)


async def test_enforced_install_applies_tuples_and_the_copy_is_usable(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch, tmp_path: Path
) -> None:
    skill_id = await _skill(db, tmp_path)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        published = await client.post(  # legacy mode publish (owner)
            f"{BASE}/items/from-skill/{skill_id}", json={"visibility": "public", "name": "inst"}
        )
        assert published.status_code == 201, published.text
        item_id = published.json()["id"]
        source = await db.get(Skill, skill_id)  # the owner reinstalls after deleting the source
        await db.delete(source)
        await db.commit()
        store = InstallStore(item_obj(UUID(item_id)))
        authz.use(client=store)
        installed = await client.post(
            f"{BASE}/items/{item_id}/install", json={"install_mode": "new_copy"}
        )
        assert installed.status_code == 201, installed.text
        copy_id = installed.json()["installed_skill_id"]
        assert any(t.relation == "owner" and t.object == f"skill:{copy_id}" for t in store.stored())
        assert (await client.get(f"/api/skills/{copy_id}")).status_code == 200


async def test_moderation_records_an_audit_decision(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    system = await add_item(db, is_system=True)
    authz.use()
    assert (await client.post(f"{BASE}/items/{system.id}/disable")).status_code == 200
    assert (await client.post(f"{BASE}/items/{system.id}/enable")).status_code == 200
    async with TestSession() as s:
        reasons = list(
            await s.scalars(
                select(AuditEvent.reason_code).where(
                    AuditEvent.action == "marketplace.super_moderation",
                    AuditEvent.target_id == str(system.id),
                )
            )
        )
    assert sorted(r or "" for r in reasons) == [
        "super_moderation.disable",
        "super_moderation.enable",
    ]


async def test_legacy_member_manage_on_platform_item_is_403_not_500(
    client: AsyncClient, db: AsyncSession, regular_user: None
) -> None:
    """Accepted deviation (review): the lazy ``acl_entries`` load used to raise 500."""
    plain = await add_item(db, is_system=True)
    with_acl = await add_item(db, is_system=True, acl={TEST_USER_ID: "view"})
    for item in (plain, with_acl):
        response = await client.patch(f"{BASE}/items/{item.id}", json={"description": "x"})
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "MARKETPLACE_MANAGE_FORBIDDEN"


async def test_patch_rejects_system_visibility_for_org_items(
    client: AsyncClient, db: AsyncSession
) -> None:
    item = await add_item(db, visibility="public", owner=TEST_USER_ID)
    response = await client.patch(f"{BASE}/items/{item.id}", json={"visibility": "system"})
    assert response.status_code == 422
