"""E-07 ``/api/tenants/{id}/models``: company models and designation (9.7, S07)."""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.organization import Tenant
from app.services import model_service
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e07_support import (
    TENANT,
    company_model,
    enforced,
    foreign_tenant,
    platform_model,
    stored,
)
from tests.authz.e09_support import MEMBER, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

MODELS = f"{TENANT}/models"
NEW = {"provider": "openai_compatible", "model_name": "qwen-72b", "display_name": "사내 Qwen"}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def test_member_is_refused_and_foreign_company_is_404(client: AsyncClient) -> None:
    member = await client.get(MODELS)
    assert member.status_code == 403
    assert member.json()["error"]["code"] == "permission_required"
    assert (await client.get(f"/api/tenants/{uuid4()}/models")).status_code == 404


async def test_admin_creates_a_designated_company_model(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    platform = await platform_model(db)
    foreign = await company_model(db, await foreign_tenant(db), "Foreign")
    created = await client.post(MODELS, json={**NEW, "allowed_org_ids": [str(TEST_ORG_ID)]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["source"], body["allowed_org_ids"]) == ("company", [str(TEST_ORG_ID)])
    row = await db.get(Model, UUID(body["id"]), populate_existing=True)
    assert row is not None and row.tenant_id == TEST_TENANT_ID
    listed = {item["id"]: item for item in (await client.get(MODELS)).json()["items"]}
    assert str(foreign.id) not in listed  # never another company's models
    assert listed[str(platform.id)]["allowed_org_ids"] is None  # platform: every org
    assert listed[body["id"]]["company_enabled"] is True
    events = [e.action for e in await actions(body["id"])]
    assert events == ["tenant.catalog_update"]
    # The company model is never listed or resolved on platform-wide paths.
    assert body["id"] not in {str(m["id"]) for m in await model_service.list_models(db)}
    assert await model_service.resolve_model(db, "사내 Qwen", strict=True) is None


async def test_platform_models_only_take_a_designation(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    platform = await platform_model(db)
    path = f"{MODELS}/{platform.id}"
    renamed = await client.patch(path, json={"display_name": "mine"})
    assert renamed.status_code == 403
    narrowed = await client.patch(path, json={"allowed_org_ids": [str(OTHER_ORG_ID)]})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["allowed_org_ids"] == [str(OTHER_ORG_ID)]
    reset = await client.patch(path, json={"allowed_org_ids": None})
    assert reset.json()["allowed_org_ids"] is None
    assert (await client.delete(path)).status_code == 403


async def test_designation_and_foreign_models_are_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    other = await foreign_tenant(db)
    stranger = await client.post(MODELS, json={**NEW, "allowed_org_ids": [str(uuid4())]})
    assert stranger.status_code == 404
    foreign = await company_model(db, other, "Foreign")
    assert (
        await client.patch(f"{MODELS}/{foreign.id}", json={"display_name": "x"})
    ).status_code == 404
    assert (await client.delete(f"{MODELS}/{foreign.id}")).status_code == 404


async def test_one_default_per_company_and_delete(client: AsyncClient, db: AsyncSession) -> None:
    await make_admin(db)
    first = (await client.post(MODELS, json={**NEW, "is_default": True})).json()
    second = await client.post(
        MODELS, json={**NEW, "model_name": "b", "display_name": "B", "is_default": True}
    )
    assert second.status_code == 201
    row = await db.get(Model, UUID(first["id"]), populate_existing=True)
    assert row is not None and row.is_default is False
    deleted = await client.delete(f"{MODELS}/{first['id']}")
    assert deleted.status_code == 204
    tenant = await db.get(Tenant, TEST_TENANT_ID, populate_existing=True)
    assert tenant is not None and first["id"] not in tenant.settings.get("model_orgs", {})


async def test_enforced_create_fans_out_to_designated_org_only(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_admin(db)
    with enforced(test_app, monkeypatch) as store:
        created = await client.post(MODELS, json={**NEW, "allowed_org_ids": [str(TEST_ORG_ID)]})
        assert created.status_code == 201, created.text
        obj = f"llm_model:{created.json()['id']}"
        assert (f"organization:{TEST_ORG_ID}#member", "consumer", obj) in stored(store)
        assert (f"organization:{OTHER_ORG_ID}#member", "consumer", obj) not in stored(store)
        # Re-designating to the sibling organization moves the grant.
        moved = await client.patch(
            f"{MODELS}/{created.json()['id']}", json={"allowed_org_ids": [str(OTHER_ORG_ID)]}
        )
        assert moved.status_code == 200, moved.text
        assert (f"organization:{TEST_ORG_ID}#member", "consumer", obj) not in stored(store)
        assert (f"organization:{OTHER_ORG_ID}#member", "consumer", obj) in stored(store)
