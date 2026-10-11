"""Catalog policy writes are scoped, versioned and constrained by company policy."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.organization import Organization, Tenant
from app.models.tool import Tool
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.fixture(autouse=True)
async def physical_model_scope(db: AsyncSession) -> AsyncGenerator[None, None]:
    await db.execute(text("ALTER TABLE models ADD COLUMN tenant_id UUID"))
    await db.commit()
    try:
        yield
    finally:
        await db.rollback()
        await db.execute(text("ALTER TABLE models DROP COLUMN tenant_id"))
        await db.commit()


async def catalog_fixture(db: AsyncSession, fixture: OrgFixture) -> tuple[Model, Tool, Model]:
    models = [Model(provider="openai", model_name=name, display_name=name) for name in ("A", "B")]
    tool = Tool(name="System tool", definition_key="builtin:current_datetime", is_system=True)
    db.add_all([*models, tool])
    await db.flush()
    await db.execute(
        text("UPDATE models SET tenant_id=:tenant WHERE id=:id"),
        {"tenant": fixture.tenant.hex, "id": models[1].id.hex},
    )
    await db.commit()
    return models[0], tool, models[1]


async def test_catalog_admin_disables_item_with_version_and_preserves_other_settings(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: catalog items and an authorized organization admin.
    model, tool, company_model = await catalog_fixture(db, org_fixture)
    await grant_admin(db, org_fixture)
    organization = await db.get(Organization, org_fixture.first)
    assert organization is not None
    organization.settings = {"unrelated": "preserved"}
    await db.commit()
    path = f"/api/orgs/{org_fixture.first}/catalog-policy"
    original = await raw_client.get(path)
    assert original.status_code == 200
    ids = {item["id"] for item in original.json()["items"]}
    assert {str(model.id), str(tool.id), str(company_model.id)} <= ids
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    # When: the organization disables one permitted catalog item.
    changed = await raw_client.put(
        path,
        json={"items": [{"type": "tool", "id": str(tool.id), "enabled": False}]},
        headers=headers,
    )
    # Then: the override persists without losing unrelated policy.
    assert changed.status_code == 200
    item = next(row for row in changed.json()["items"] if row["id"] == str(tool.id))
    assert item["enabled"] is False
    await db.refresh(organization)
    assert organization.settings["unrelated"] == "preserved"
    stale = await raw_client.put(path, json={"items": []}, headers=headers)
    assert stale.status_code == 409


async def test_company_disabled_items_cannot_be_enabled_by_organization(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: a company restriction on a system tool.
    _, tool, _ = await catalog_fixture(db, org_fixture)
    await grant_admin(db, org_fixture)
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.settings = {
        "catalog_policy": {"items": [{"type": "tool", "id": str(tool.id), "enabled": False}]}
    }
    await db.commit()
    path = f"/api/orgs/{org_fixture.first}/catalog-policy"
    original = await raw_client.get(path)
    item = next(row for row in original.json()["items"] if row["id"] == str(tool.id))
    assert item["enabled"] is False and item["company_enabled"] is False
    # When: the organization attempts to lift the company restriction.
    result = await raw_client.put(
        path,
        json={"items": [{"type": "tool", "id": str(tool.id), "enabled": True}]},
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    # Then: the write fails with the policy lock contract.
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "catalog_policy_locked"


async def test_catalog_rejects_private_foreign_and_missing_items_without_enumeration(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: private tools and another company's model.
    _, _, company_model = await catalog_fixture(db, org_fixture)
    await grant_admin(db, org_fixture)
    await db.execute(
        text("UPDATE models SET tenant_id=:tenant WHERE id=:id"),
        {"tenant": org_fixture.second.hex, "id": company_model.id.hex},
    )
    private = Tool(
        name="Private tool",
        definition_key="builtin:current_datetime",
        user_id=org_fixture.user.id,
        org_id=org_fixture.first,
        tenant_id=org_fixture.tenant,
    )
    db.add(private)
    await db.commit()
    path = f"/api/orgs/{org_fixture.first}/catalog-policy"
    original = await raw_client.get(path)
    assert str(company_model.id) not in {item["id"] for item in original.json()["items"]}
    # When: callers submit an inaccessible or nonexistent catalog item.
    for kind, key in (("model", company_model.id), ("tool", private.id), ("tool", uuid4())):
        result = await raw_client.put(
            path,
            json={"items": [{"type": kind, "id": str(key), "enabled": False}]},
            headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
        )
        # Then: the same non-enumerating error is returned.
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "not_found"


async def test_catalog_member_and_csrf_guards_precede_policy_mutation(
    raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an ordinary member without policy governance.
    path = f"/api/orgs/{org_fixture.first}/catalog-policy"
    # When: the member submits writes with and without CSRF protection.
    result = await raw_client.put(path, json={"items": []}, headers=org_fixture.session.headers())
    missing_csrf = await raw_client.put(path, json={"items": []})
    # Then: both fail before any policy mutation.
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "permission_required"
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_mismatch"
