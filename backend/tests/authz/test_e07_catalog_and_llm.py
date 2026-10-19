"""E-07 company catalog policy and service LLM (9.7, 8.9, S07)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    get_setting,
    resolve_system_model,
)
from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e07_support import (
    TENANT,
    credential,
    foreign_tenant,
    platform_llm,
    platform_model,
    set_limit,
    system_tool,
)
from tests.authz.e09_support import MEMBER, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

CATALOG, LLM = f"{TENANT}/catalog-policy", f"{TENANT}/llm-settings"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def test_member_is_refused(client: AsyncClient) -> None:
    for path in (CATALOG, LLM):
        assert (await client.get(path)).status_code == 403


async def test_company_policy_turns_items_off_but_cannot_lift_platform_hiding(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    tool = await system_tool(db)
    hidden = await platform_model(db, "Hidden", is_visible=False)
    current = await client.get(CATALOG)
    assert current.status_code == 200, current.text
    etag = current.headers["ETag"]
    locked = await client.put(
        CATALOG,
        json={"items": [{"type": "model", "id": str(hidden.id), "enabled": True}]},
        headers={"If-Match": etag},
    )
    assert locked.status_code == 403
    assert locked.json()["error"]["code"] == "catalog_policy_locked"
    off = {"items": [{"type": "tool", "id": str(tool.id), "enabled": False}]}
    stale = await client.put(CATALOG, json=off, headers={"If-Match": '"x"'})
    assert stale.status_code == 409
    changed = await client.put(CATALOG, json=off, headers={"If-Match": etag})
    assert changed.status_code == 200, changed.text
    assert changed.headers["ETag"] != etag
    item = next(row for row in changed.json()["items"] if row["id"] == str(tool.id))
    assert (item["platform_available"], item["company_enabled"]) == (True, False)
    events = [e for e in await actions(TEST_TENANT_ID) if e.action == "tenant.catalog_update"]
    assert len(events) == 1


async def test_company_override_wins_and_platform_is_the_fallback(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    platform = await credential(db, tenant_id=None, key="sk-platform")
    company = await credential(db, tenant_id=TEST_TENANT_ID, key="sk-company")
    await platform_llm(db, platform.id)
    inherited = (await client.get(LLM)).json()["items"][0]
    assert (inherited["role"], inherited["inherits"], inherited["configured"]) == (
        "text_primary",
        True,
        True,
    )
    override = {"role": "text_primary", "credential_id": str(company.id), "model_name": "qwen"}
    changed = await client.put(LLM, json=override)
    assert changed.status_code == 200, changed.text
    slot = changed.json()["items"][0]
    assert (slot["inherits"], slot["credential_id"], slot["model_name"]) == (
        False,
        str(company.id),
        "qwen",
    )
    resolved = await resolve_system_model(db, "text_primary", tenant_id=TEST_TENANT_ID)
    assert (resolved.api_key, resolved.model_name) == ("sk-company", "qwen")
    platform_slot = await resolve_system_model(db, "text_primary")
    assert (platform_slot.api_key, platform_slot.model_name) == ("sk-platform", "gpt-platform")
    platform_row = await get_setting(db, "text_primary")
    assert platform_row is not None and platform_row.credential_id == platform.id
    dropped = await client.put(LLM, json={"role": "text_primary"})
    assert dropped.json()["items"][0]["inherits"] is True
    events = [e for e in await actions(TEST_TENANT_ID) if e.action == "tenant.llm_settings_update"]
    assert len(events) == 2


async def test_without_platform_keys_there_is_no_fallback(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    platform = await credential(db, tenant_id=None, key="sk-platform")
    foreign = await credential(db, tenant_id=await foreign_tenant(db), key="sk-foreign")
    await platform_llm(db, platform.id)
    await set_limit(db, allow_system_models=False)
    slot = (await client.get(LLM)).json()["items"][0]
    assert (slot["inherits"], slot["configured"]) == (True, False)
    with pytest.raises(SystemModelNotConfiguredError):
        await resolve_system_model(db, "text_primary", tenant_id=TEST_TENANT_ID)
    for refused in (platform.id, foreign.id):
        body = {"role": "text_primary", "credential_id": str(refused), "model_name": "m"}
        assert (await client.put(LLM, json=body)).status_code == 404
    half = await client.put(LLM, json={"role": "text_primary", "model_name": "m"})
    assert half.status_code == 422
